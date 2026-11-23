# Timeline resize integrity and latency flags

## Scope and root cause

The November 13, 2026 fix is compared with `a58b600ea10a`. It fixes the
detail-panel drag path, not the entire cost of opening a large report.

Previously `resize()` assigned both canvas backing-store dimensions even when
they had not changed. Assigning either dimension clears the canvas immediately.
The drag handler called `resize()` in one animation callback, but `draw()` queued
painting for the next callback. Interleaving repeated pointer moves with painting
therefore exposed a cleared chart and ruler. Merely drawing less often would not
fix this ordering error.

`resize()` now updates CSS geometry and requests painting. Backing-store sizes
are synchronized only when different, immediately before the full chart/ruler
paint in the same callback. Initial font measurement still runs if the initial
canvas dimensions already match. Selection, time range, device pixel ratio
and fractional-pixel handling are retained. Ending, cancelling or
losing pointer capture removes all four drag listeners and cancels the pending
resize callback. Previously the unused once-only cancellation handler survived
each normal drag completion.

On short touch landscape screens, divider frames with the same clamped detail
width and limits skip the resize and chart paint. A real width or device-pixel
ratio change still updates the chart and ruler. In two before/after pairs with
the 58,539-call `ping3` report at 844×390, 24 touch moves beyond the 280px
limit caused 24 full track paints before and zero after, per run. Reversing
direction resumed painting once the width changed. This counts track paints,
not end-to-end frame latency.

## Flags are retained, not reconstructed

The reported kernel-only `write4.html` already contained latency flags on all
29,586 calls. The default Context colors, irqs-off hatching, need-resched marks
and optional Preempt depth overlay still use the same flags. Their explicit
text was inside the collapsed **Trace metadata** section. It now appears in the
main call facts without expanding that section, retaining the original preempt
count and trace-baseline explanation.

`none` is shown only for recorded, clear latency flags. Calls without latency
data do not get a flags row; missing data is not converted to zero. No parser,
BTF interpretation, context inference, baseline, statistics, recorded address,
source position, argument or return value is changed by this fix.

## Real-report comparison

The original 65,648,506-byte report was left untouched. A sibling
`/vol_1t/Qemu/x86_64/share/write4.fixed.html` substitutes only the embedded
`assets/timeline/render.js` and `assets/timeline/panels.js`. Reversing those exact
substitutions reproduces the original file byte for byte, including every Text
row, source block and JSON payload. Original SHA-256:

```text
6802111a869b7d85b701504dd40644badc477d0c14eb6d9b90a90faf675de1d3
```

Three before/after pairs ran serially in independent Chromium 145.0.7632.6
processes at 1500 × 900, alternating the order on the second pair. Each run
selected the first call, made five four-step drags and requested twelve
same-size resizes. A deliberately interleaved animation callback sampled both
canvas alphas after the drag's resize callback and before the deferred painter.
The complete serialized `fgData.read('tlData')`, selection and time range were
checked before and after interaction and across reports.

| Observation | Before | After |
| --- | ---: | ---: |
| Clear chart/ruler samples during 60 sampled drag frames | 60 | 0 |
| Backing-store assignments for twelve same-size resizes, per run | 24 width + 24 height | 0 |
| Active cancellation listeners after five drags, separate once-aware pair | 5 | 0 |
| Flags text visible without expanding metadata | no | yes |
| Recorded calls / calls with latency data | 29,586 / 29,586 | unchanged |
| Text rows / DOM elements after selecting the call | 38,938 / 596,830 | unchanged |
| Calls with irqs off / need-resched | 2,430 / 30 | unchanged |
| Complete Timeline JSON identity, selection and time range | baseline | identical |
| JavaScript errors in all three runs | 0 | 0 |

The trace also retains its 58 softirq and 189 hardirq calls, one BH-off call,
raw preempt counts and baseline of one. Existing timestamp-clamping diagnostics
are not changed or claimed to be fixed here. Dense overview pixels still need
zooming to distinguish individual slices; no recorded calls are removed.

For transparency, median file-open time was 7,797.3 ms before and 7,692.6 ms
after; median first Timeline readiness was 475.6 ms and 463.0 ms respectively.
Three local pairs do **not** establish a general startup-performance improvement.
The large single-file Text/source DOM remains expensive to construct. This fix
does not hide that cost by truncating calls, stripping sources/arguments,
changing default rendering modes or disabling features.

Raw evidence is in `build/validation/20261113-timeline-resize/`:
`reproduction.json`, `write4-flags.json`, `write4-comparison.json`, the before/after
screenshots and `sample-runtime-identity.json`. The first comparison's listener
instrumentation counts registrations without explicit removal, including
callbacks automatically removed by native `once`. It is not an active-listener
count. `write4-listener-cleanup.json` uses wrappers that account for native
`once` removal, and the regression test covers normal completion, cancellation
and real pointer-capture loss.

## Reuse task positions on resize

November 19, 2026. Changing the chart/detail split, resizing the viewport or
returning from Text now reuses task positions and the visible-track index.
Only the viewport dimensions and scroll extent need updating. Filtering,
expanding or collapsing tracks, and changing inline depth still rebuild the
track layout. The ruler and canvas both use the track viewport's width after
the spacer changes, including when filtering removes or restores a scrollbar.

In a Chromium fixture with 4,096 tasks, twelve same-size resize calls, a
Text round trip and a keyboard split resize dropped from 114,688 assignments
to task `y`/`h` to zero. The test checks the selection, zoom, scroll anchor,
canvas height, adjusted scroll range and hover target. A separate regression
checks ruler and canvas alignment with a visible scrollbar and on both sides
of a filter toggle. The assignment count measures avoided work, not elapsed
time or a general startup speedup.

### Reuse filter results when showing or hiding tracks

With 4,096 task tracks and one call per track, applying an alternating-task
filter with **Hide unmatched** off still performs 4,096 line membership
checks, but it no longer rewrites the 4,096 track positions. Three visibility
toggles after applying that filter now make zero line membership checks
(previously 12,288). A visibility change still updates track positions, but
keeps the top task and its scroll offset if it survives; otherwise it starts
at the next shown track where possible. Search, analyses and CSV stay on the
same set of matching calls. Browser tests verify the retained range-summary
rows, exact scroll offsets and selection behavior separately from work counts.

### Retain Timeline results for equivalent Text filters

November 21, 2026. A new Text expression can change the selected Text lines
without changing which recorded calls match. Timeline keeps its existing call
mask, Find intersection, error-navigation position and chart in that case.
The range summary also keeps its current rows and page. The new expression
still appears in the Filter controls and in new exports; a pending range CSV
is canceled when its recorded expression would change. Other open analyses
can refresh their export state on an expression change. An empty result
remains distinct from Clear.

The Timeline does not use Text navigation's nearest-call fallback to project
matches. A matching entry or nonzero exit line includes the call, including
all calls that share a line. An empty result skips line checks; for an
identical set of at most 64 lines the previous projection is reused without
walking the calls. Otherwise Timeline checks the exact call membership.

`tests/benchmark_filter_projection.py` measures Timeline after the Text query
has finished. On `ping3` (58,539 calls, identical data identity), four
alternating Chromium runs at 4× CPU throttling used a range summary over the
first 1% of the time span. With a two-line equivalent result, membership
checks went from **80,922 to 2**, median synchronous work from **5.9 to 0.1
ms**, and the scheduled chart callback from **7.3 ms to none**. The range row
stayed mounted. Applying a different sparse or dense result and clearing the
filter showed no comparable speedup. Callback time is not browser paint time,
and the benchmark excludes Text query preparation and Text rendering. Raw
samples: `build/benchmarks/20261121-filter-projection/final.jsonl`.

## Height-only resize follows the track layer

Resizing the detail pane still repaints the track canvas at its new height.
It no longer redraws the ruler solely because the track height changed. If
the track scrollbar changes the available width, or the pixel ratio changes,
the ruler still repaints. Pan, zoom, selection and theme redraws are unchanged.

Opening a detail pane updates the chart split without remeasuring the page or
refitting the toolbar. The pane still clamps to the available height, updates
the keyboard separator's value, and synchronizes the chart before revealing
the selected row. Window resize and fullscreen still use the full layout path.

In five alternating Chromium runs on `ping3` at 4× CPU throttling, the first
click to two animation frames fell from 75.3 to 70.3 ms with Text unopened,
and from 77.3 to 75.2 ms after enabling Full Text. Selection and split heights
matched. These local measurements are about the first open, not repeated
selection. A separate experiment to cache merged dense rows did not improve
click-to-frame latency consistently and was not kept. Raw runs are in
`build/benchmarks/20261119-trace-jump/first-click-intermediate-ab.jsonl` and
`build/benchmarks/20261119-trace-jump/dense-row-plan-ab.jsonl`.

Three successive keyboard resizes produced **three → zero ruler paints**
in Chromium at DPR 1, 1.25 and 2, across standalone and sidecar reports. The
ruler pixels and trace state stayed identical; changing the viewport width
still repainted both canvases. This measures paint work, not frame latency.
The cursor regression counts the two canvas layers separately: a cursor-only
move leaves both untouched; a zoom queued in the same frame repaints both
and matches the subsequent explicit redraw pixel for pixel.

### Batch detail-split drag input

Dragging the detail divider keeps only the latest pointer position for the
next frame. The preview clamps against the current viewport once, rather than
reading geometry and storing a preference for each move. Ending the gesture
keeps the last displayed split and saves it once; a release before the preview
frame applies the last position first. Switching away from Timeline, closing
details or expanding the pane discards an unfinished preview. Keyboard resizing
and double-click reset keep the same limits and persistence behavior.

With the same offline `ping3` report in Chromium 145 at 4× CPU throttling, five
paired runs dispatched a burst of 120 synthetic moves after a real pointer-down.
Toolbar height reads across preview and release fell from 122 to 1, and writes
of the split preference fell from 120 to 1. The dispatch-and-handler median
fell from 5.2 to 1.5 ms. Both versions previewed and saved the same 328 px split;
the canvas remained painted at the sampled frame. This measures dense input
handling rather than end-to-end drag latency or physical display timing. Logs
are in `build/benchmarks/20261119-trace-jump/split-drag-ab.jsonl`.

## Repeated selection on a large report

Selecting a slice replaces its detail and fills its initial source context
before measuring the pane. The recorded and inline slice headers use their
observed border-box height rather than reading computed style on each
selection. Headers that flow in narrow or short views contribute no sticky
inset. Analysis tables retain their synchronous header measurement for paging
and focus navigation; browsers without `ResizeObserver` also keep a direct
measurement path.

In the original 128,864,203-byte `ping3.html` report, 40 distinct slice
selections in Chromium went from a 103.5 ms to a 78.6 ms median from click to
two animation frames. The hidden Text tree still made style recalculation
expensive in that build. For large traces, `--report-mode sidecar` and `fgview`
also reduce the amount of Text held in memory:
the Text rows load on demand, and Windowed Text keeps the mounted rows bounded
after visiting the Text tab. The generated sidecar on the same trace measured
29.3 ms with Timeline opened first and 28.7 ms after visiting Windowed Text,
with no 50 ms main-thread tasks recorded in either 40-click run. The final
single-file run still recorded long tasks. These measurements show click
latency, not a memory leak or a monotonically increasing slowdown. Local
benchmark logs are in `build/benchmarks/20261119-timeline-slice-click/`.

Clicking the already-selected canvas slice leaves its visible detail mounted,
including any open disclosures. Selecting a different slice, returning from an
analysis panel, and double-click zoom still run their normal actions. On the
same `ping3` standalone report, 40 clicks on the same slice went from 76.2 ms to
29.7 ms median. The first click still opened the pane; the remaining 39 reused
it. Recorded main-thread long tasks went from 40 to 1. A separate 40-distinct-
slice run was still 77.9 ms median, so this does not replace the sidecar
recommendation for browsing many different calls.

Isolation runs found that dropping only the synchronous pane-height read moved
the style work into the browser's rendering phase without reducing total
latency. Removing the hidden Text tree cut click costs, but removing it in one
step took about half a second on this report. Replacing its display lock with
`display: none` also made Text-to-Timeline switching slower in the round-trip
probe. Neither change is enabled. Logs are in
`build/benchmarks/20261119-timeline-slice-residual/`.

### Keep native disclosure markers without updating list counters

Chromium's native `summary` style includes `counter-increment: list-item 0`.
Replacing slice details with source or other disclosures can cause Chromium to
walk the document to update counters, even while Full Text is hidden. The
detail pane now overrides `counter-increment` on its `summary` elements only.
Their native markers, keyboard interaction and source loading remain intact.
The Text tree stays mounted; returning to it does not need to recreate rows,
expanded sources or the user's selection.

In Chromium 145, two runs per variant clicked 40 distinct slices after
mounting all 82,529 Text rows in the offline `ping3` report. The A/B reports
differed only by the counter override. The click-to-two-frames median went
from **46.5–47.0 ms** to **29.1–29.3 ms**, and cumulative style recalculation
fell from **1.56–1.61 s** to **25–26 ms** per run. The control recorded one or
two main-thread long tasks; the fixed runs recorded none. This measures the
specific large-document cost, not cumulative slowdown or memory use. Logs and
the round-trip probe are in `build/benchmarks/20261119-timeline-full-retained/`.

### Sustained slice selection on the original `ping3.html`

A longer Chromium 145 run at 4× CPU throttling repeated the same 20 actual
mouse clicks per batch and verified the selected slice on every click. Full
Text runs first verified all 82,529 rows were mounted; the Windowed Text run
visited Text before returning to Timeline. The latency columns exclude the
first batch (including the cold first open). Style time is averaged over all
clicks, including the first batch.

| Report and Text mode | Clicks | Mounted Text rows | Mouseup handler median | Two-frame median / p95 | Style time per click |
| --- | ---: | ---: | ---: | ---: | ---: |
| Original `ping3.html`, full | 120 | 82,529 | 218.7 ms | 235.2 / 289.7 ms | 217.7 ms |
| `ping3-detail-split-only.html`, full | 240 | 82,529 | 4.1 ms | 25.9 / 31.9 ms | 2.5 ms |
| `ping3-detail-split-only.html`, windowed | 240 | 160 | 4.0 ms | 27.1 / 31.2 ms | 2.3 ms |

This is a comparison of report versions, not the effect of the split layout
change alone. Applying only the `summary` counter override in memory to the
original report yielded two warm batches with handler medians of 8.4 and
8.8 ms, without reducing the mounted Text rows. Node and listener counts
fluctuated during selection and fell after collection; these runs establish
an expensive style update in the original, not a leak or a progressive
slowdown. The two-frame numbers measure animation callbacks, not physical
display latency.

Reproduce with the same visible slice indices in either report:

```sh
indices=665,2366,3273,11321,16444,19900,23501,35149,36215,40540,42706,44443,45123,45641,47209,48384,56744,1941,3267,10712
python3 tests/benchmark_timeline_selection.py /vol_1t/Qemu/x86_64/share/ping3-detail-split-only.html \
  --indices "$indices" --text full --rows 82529 --batches 12
```

Use `--text windowed` to visit Windowed Text first. Raw results are in
`build/benchmarks/20261119-trace-jump/long-selection-verified-original.jsonl`,
`long-selection-verified-full.jsonl`, `long-selection-mounted-windowed.jsonl`
and `long-selection-original-counter.jsonl` in the same directory.

### Reuse the pane size when replacing a slice detail

Replacing one slice detail with another no longer reads the pane's
`clientHeight` synchronously on browsers with `ResizeObserver`. The existing
observer watches the pane as well as its current header, so real height
changes still update the short-pane layout. Opening the pane, switching from
analysis, and the fallback without `ResizeObserver` retain direct measurement.

In the same offline `ping3` trace, two rounds per version and entry path
clicked 40 different slices each, either directly in Timeline or after
mounting Full Text. Excluding the first three selections per run, synchronous
pane-height reads in the mouseup handler went from one per selection to zero.
Median handler time fell from **2.4–2.6 ms** to **1.2–1.3 ms**. The click-to-two-
frames median stayed at approximately **29 ms**; cumulative layout time did
not fall, so this reduces work in the input handler, not the measured time to
paint. Logs are in `build/benchmarks/20261119-trace-jump/detail-click-*.jsonl`.

Two first-open experiments were not adopted: resizing an empty pane before
inserting the detail, and sizing the split before showing the pane. On the
windowed `ping3` report (Chromium 145, 4× CPU throttling, five alternating
pairs per entry path), the latter removed one layout pass but left the median
time from mouseup to two animation frames at about 75 ms both with and without
Full Text retained. The former added a layout pass. Logs are in
`build/benchmarks/20261119-trace-jump/first-slice-*-ab.jsonl`.

### Compact the line-to-source index

The source lookup accepts a sparse JSON object, a numeric array, or a packed
string, indexed by the original one-based trace line. The dense formats store
source ID plus one; zero means no source. Reports use a dense format only with
at least 4,096 source entries and at least half their slots populated. Sparse
traces therefore do not reserve storage proportional to their highest line
number. The index remains lazy, and source blocks keep the same IDs and
loading behavior.

In an otherwise identical offline `ping3` report, the index shrank from
699,576 to 287,463 bytes, with all line lookups preserved. In five alternating
pairs in Chromium 145 at 4× CPU throttling, the median first index read was
6.8 → 3.2 ms when opening Timeline directly, and 6.5 → 2.7 ms with Full Text
retained. For the sampled first slice, mouseup to two animation frames went
from 79.1 to 73.8 ms and from 73.8 to 70.3 ms, respectively. This does not
measure repeated-selection latency. Samples are in
`build/benchmarks/20261119-trace-jump/source-line-index-ab.jsonl`.

For dense indexes with source IDs from 0 through 4,094, each slot now uses two
URL-safe Base64 digits in a JSON string. The browser reads the two characters
for the selected line without parsing or allocating a full numeric array.
Higher IDs still use the array. On `ping3`, the index shrank from 287,463 to
165,060 bytes. In 12 alternating A/B pairs at 1500 × 900 (Chromium 145,
4× CPU throttling, with Windowed Text mounted), median first-read time fell
from 3.0 to 0.6 ms. The first-click handler median fell from 19.0 to 16.2 ms;
the two-frame timing proxy fell from 62.8 to 58.6 ms. All paired clicks
resolved the same source IDs. The frame proxy is not display latency and
these measurements do not describe repeated clicks. Samples:
`build/benchmarks/btf-value-layout/selection-followup/cold-source-ab.jsonl`.

### Keep slice selection separate from search state

Changing the selected slice updates only the position in a ready search.
An empty, paused or in-progress search leaves the toolbar alone. The search
workflow still owns its accessible state, navigation controls and hints.
The counter preserves the total on a non-match and shows position / total on
a match. Browser coverage checks the resulting mutations as well as the text.

In Chromium 145, selecting the same sequence of 40 distinct visible slices
in `ping3` went from 160 search-toolbar mutations (unchanged `title`,
`aria-invalid` and `aria-busy`) to zero without an active query, both directly
in Timeline and after mounting Full Text. No 50 ms tasks were observed in
these runs. The baseline and the change had similar click-to-frame medians
and neither showed increasing latency over the 40 clicks. These runs confirm
avoided DOM work, not a general frame-time or memory improvement. Logs are in
`build/benchmarks/20261119-trace-jump/selection-search-ab.jsonl`.

### Size the track viewport without another layout pass

CSS keeps the track canvas as tall as the scrollport and sizes the spacer to
the remaining track content. Track layout sets the total content height only
when it changes; moving the detail split no longer rewrites the CSS heights
of either canvas or the spacer. The renderer still uses the scrollport's
measured dimensions to size its DPR-adjusted backing store and align the ruler.

On offline `ping3` with Chromium 145 at 4× CPU throttling, five alternating
A/B pairs per entry path opened the same first slice. Browser layout count
fell from six to five in every pair, with the same selection, detail and
canvas heights, and scroll offset. From mouseup to the second animation frame,
the median was 87.0 → 79.8 ms when opening Timeline directly and 80.8 →
82.4 ms after mounting Full Text. One Full Text candidate run took 247.3 ms;
this does not establish a latency improvement with retained Full Text or a
fix for progressive slowdown. The browser regression also checks sticky
canvas position and the exact scroll extent with and without overflow in
both report delivery modes. Raw measurements are in
`build/benchmarks/20261119-trace-jump/css-track-height-ab.jsonl`.

### Pause chart painting while details are expanded

Expanding details hides the track and ruler canvases. Call navigation and zoom
still update selection and view state, but do not schedule chart painting; a
frame already queued before expansion skips painting as well. Restoring the
split redraws the chart with the current selection and zoom. The duration
histogram in the visible detail pane still redraws independently.

In two alternating Chromium A/B pairs on the offline `ping3` report with 4×
CPU throttling, 20 selections in expanded details went from 20 track paints
and 20 ruler paints per run to zero. After restoring the split, the selected
call, time range and both canvas images matched. The measurement does not
establish a faster selection handler or cover clicks on the visible chart.
Raw data is in `build/benchmarks/20261119-trace-jump/expanded-chart-paused-ab.jsonl`.

The expanded-detail regression checks track background fills, ruler bitmap
copies and both canvas bitmaps. It no longer treats a transform reset as a
paint: the track renderer can reuse its transform across visible frames.
The thin-slice hover regression likewise measures track fills and checks
that the canvas pixels stay unchanged while only the tooltip moves.

## Regression and use

```sh
PYTHONPATH=.:tests python3 -m unittest test_timeline_resize -v
PYTHONPATH=.:tests python3 -m unittest \
  test_browser.BrowserSmokeTest.test_timeline_flags_are_visible_without_opening_trace_metadata \
  test_browser.BrowserSmokeTest.test_timeline_zero_latency_flags_are_explicit_not_missing \
  test_browser.BrowserSmokeTest.test_timeline_user_calls_do_not_invent_unrecorded_kernel_flags \
  test_browser.BrowserSmokeTest.test_timeline_calls_without_latency_data_do_not_show_false_flags
```

Resize coverage includes single/sidecar reports, light/dark themes, DPR
1/1.25/2, rapid drag frames, repeated same-size resizes, repeated normal/cancelled
drags, lost capture, pending-callback cancellation, double-click reset and
fullscreen round trips. Adjacent browser tests compare the canvas pixels before
and after cursor-only moves and full redraws at fractional/high DPR. Data
equality checks retain the entire Timeline model, not just counts.

The initial 527-test adjacent/browser run exposed only the old layout test's
two single/sidecar subtest expectations that flags must be hidden. Those
expectations were updated to verify visible flags through metadata toggles,
unchanged recorded data and readable raw-only arguments. All 284 adjacent tests
then passed, including the new initial-font-metrics test; the 244 browser tests
had passed in the initial run against the same production code. A final
11-test resize/flags/cursor suite also passed. Both samples, the guide screenshot,
assembled JavaScript syntax and 206 local Markdown links were verified.

`sample.html` and `sample_mixed.html` receive the same runtime-only substitutions,
with their embedded trace/source data unchanged. Existing standalone HTML
contains its own runtime: installing updated FuncGraph does not rewrite old
reports. Regenerate with the original source/trace options to receive UI fixes;
there is no need to omit Text, source information or arguments. Sidecar remains
an optional full-data delivery mode, not a prerequisite for this repair.

### Defer collapsed call stacks on compact touch layouts

The detail pane shows the recorded-frame count and complete CSV export
without building stack rows. First expansion builds the first page; hiding
it again keeps loaded pages for that selection. Desktop still shows the
stack immediately. Error-chain display and recorded source evidence are
unchanged.

Using the same `ping3` data and 22 visible selected slices in both variants,
Chromium 145 at 844×390 with 4× CPU throttling completed two 198-tap runs per
variant in alternating order (baseline, candidate, candidate, baseline).
Each tap was checked against the actual selected slice. The table shows
warm results after excluding the first batch of each run.

| Per-tap measurement | Before | After |
| --- | ---: | ---: |
| Median rendered stack rows | 15.5 | 0 |
| Median elements under the stack section | 143.5 | 7 |
| Median click-handler time | 12.3–14.8 ms | 7.2–7.5 ms |
| Average cumulative layout time per tap | 6.0–7.2 ms | 2.7–2.8 ms |
| Median click-to-two-animation-frames time | 31.5–31.6 ms | 31.5–31.6 ms |

This reduces selection work when the stack is not read; it does not establish
a lower median paint latency. The click timing for touch starts with the
synthesized `click`, not the initial contact. Logs are in
`build/benchmarks/20261120-compact-stack/final-*.jsonl`. Reproduce on either
report by passing its filename to the same command:

```sh
indices=40710,40685,40683,40680,47465,3298,40679,47452,3297,40671,47444,3295,3527,36146,40663,47435,2384,3294,3521,36145,40661,47434
python3 tests/benchmark_timeline_selection.py /vol_1t/Qemu/x86_64/share/ping3-compact-stack.html \
  --viewport 844x390 --touch --scroll-top 235 --indices "$indices" --batches 9 --cpu-rate 4
```

### Reuse dense drawing runs while the viewport is stable

For repeat paints of the same horizontal viewport, rows containing dense
pixels can reuse ordered drawing runs. The cache retains geometry and the
filter-selected representative, not painted pixels or colors. The existing
renderer still paints hover, errors, selection, and the current theme; hit
testing still uses the original row data. Changing the match set invalidates
the paint runs. A new viewport skips recording on its first paint, and at
most 32,768 paint commands are retained for any one viewport.

In Chromium 145 at 4× CPU throttling, instrumenting the track `render()`
body during repeated real `ping3` selections gave these warm medians across
two runs per variant:

| Track rendering | Before | After |
| --- | ---: | ---: |
| Desktop, 1500×900 | 3.4 ms | 2.9 ms |
| Touch, 844×390 | 1.1–1.2 ms | 0.9–1.0 ms |
| Desktop, scripted viewport changes | 2.6–2.7 ms | 2.4–2.5 ms |

These are rendering measurements, not visible click-to-paint latency. The
baseline and candidate rendered the same pixels in 12 sampled desktop and
touch states, including selection, filtering, theme changes, and zoom.
Logs are in `build/benchmarks/20261120-desktop-click/final-paint-ab.jsonl`.

### Defer offscreen source excerpts on compact touch layouts

Selecting a recorded call keeps the existing source disclosure and its open
preference, but builds an open excerpt only when its heading enters the
detail pane or the reader explicitly opens it. The **Show source excerpt**
action requests it immediately. Desktop and synthetic-frame loading are
unchanged. Source-position facts and links are distinct from the excerpt.

Using 22 visible `ping3` slices at 844×390 in Chromium 145 with 4× CPU
throttling, two 154-tap runs per variant (baseline, candidate, candidate,
baseline) gave the following warm medians after dropping each first batch:

| Per-tap measurement | Before | After |
| --- | ---: | ---: |
| Mounted source lines | 11 | 0 |
| Descendant elements in the excerpt | 26 | 0 |
| Click-handler time | 6.2–6.5 ms | 5.3–5.4 ms |
| Click to two animation frames | 31.9 ms | 31.9–32.0 ms |

The last measure does not show an improvement in frame latency. Each tap
verified the selected slice. Logs are in
`build/benchmarks/20261120-source-on-view/`; the same command and indices as
the compact-stack benchmark apply to `ping3-detail-shortcuts.html` and
`ping3-source-on-view.html`, with `--batches 7`.

### Defer offscreen source excerpts on desktop too

Desktop now follows the same rule for recorded calls: keep source position
and the disclosure, but build the excerpt when its open heading enters the
pane or the reader explicitly opens it. Inline synthetic frames and browsers
without `IntersectionObserver` retain their previous behavior.

With the desktop selection indices above, Chromium 145 at 1500 × 900 and 4×
CPU throttling clicked the same 20 `ping3` slices in each of seven batches.
Starting with Text unmounted, the reports were run in before/after/after/before
order. The warm results exclude the first batch of each run:

| Per-click measurement | Before | After |
| --- | ---: | ---: |
| Median mounted excerpt rows / elements | 22 / 49 | 0 / 0 |
| Median mouseup handler time | 3.3–3.4 ms | 3.0–3.1 ms |
| Average cumulative layout time | 3.74–4.04 ms | 3.44–3.58 ms |
| Median mouseup to two animation frames | 26.2–26.7 ms | 29.6–29.7 ms |

This reduces offscreen DOM work, not the measured two-frame latency. Scrolling
to slice 665's source heading yielded the same 22 lines, source location and
URL without changing the selection or time range. Raw results are in
`build/benchmarks/20261120-desktop-source-on-view/`, comparing
`ping3-direct-symbol.html` and `ping3-desktop-source-on-view.html`.

### Reuse opacity within a dense paint row

Consecutive dense runs with the same opacity keep the Canvas state rather
than resetting and rewriting it for every rectangle. Full slices still set
their own opacity, outlines are opaque, and the renderer restores opacity
after each row. Neither rectangle order nor filter/selection highlighting
changes.

One warm desktop `ping3` frame made 2,201 → 1,003 opacity assignments with
the same 2,124 rectangle fills. At device-pixel ratios 1, 1.25, and 2, all
24 sampled Canvas images were identical, including hover, selection, theme,
filters and zoom. At 1500 × 900 with 4× CPU throttling, two alternating runs
per variant measured 140 warm paints each: median track-render time went
from 3.2–3.3 ms to 2.9–3.0 ms. This times the drawing function, not input to
screen presentation. Samples: `build/benchmarks/20261120-dense-opacity/`.

Within a row, consecutive dense runs also keep the current fill color when
it is unchanged. A full slice can paint labels or markers with other colors,
so the next dense run sets its color again. Colors still follow the current
palette on cached drawing runs.

Across 20 warm `ping3` selections, dense-run fill assignments fell from
16,260 to 14,900 with the same rectangle count. All 60 track and ruler frame
pairs matched at DPR 1, 1.25 and 2. Three alternating runs per variant at
4× CPU throttling showed overlapping median draw times (2.9–3.0 ms per
paint). This reduces Canvas state writes, not measured latency.
Samples: `build/benchmarks/20261120-selection-paint-work/`.

### Defer collapsed stacks in small mouse windows too

The compact stack disclosure now depends on window size, not the pointer
type. The **Actions** shortcut opens and focuses it, and Tab/Shift+Tab
between the destination heading and **← Call details** scroll only the pane.
Neither stack CSV nor the open stack in larger windows changes.

At 844 × 390 with 4× CPU throttling, two alternating `ping3` runs per
variant opened details on slice 665, then clicked the same 20 visible
slices in eight batches. After dropping the first batch, the median stack
went from five rows and 49 descendant elements to zero rows and seven
elements. Median mouseup handler time went from 2.5 ms to 2.0–2.1 ms;
average cumulative layout time per click went from 2.66–2.70 ms to
1.77–1.78 ms. Mouseup to two animation frames stayed around 31 ms.
Raw measurements: `build/benchmarks/20261120-compact-pointer-stack/primed.jsonl`.

### Reuse slice paint styles across frames

The color cache retains the fill, text and border tuple, so a warm redraw
does not allocate one tuple per slice or dense run. Inline, ghost and
error styles share palette tuples. Changing the palette or color mode
still invalidates the relevant cache, and the console probe returns a copy.

In one warm desktop `ping3` frame, 1,318 style lookups referenced 394
cached tuples instead of allocating 1,318 separate arrays. At 844 × 390
the same comparison was 81 lookups using 38 tuples. All 12 sampled canvas
images matched pixel for pixel across selection, zoom and theme changes
at device-pixel ratios 1, 1.25 and 2. Two alternating 100-click desktop runs
per variant at 4× throttling showed no measurable median input improvement:
the warm handler stayed near 3.1 ms and the two-frame proxy near 29–30 ms.
Raw click measurements: `build/benchmarks/20261120-reused-paint-styles/`.

### Long-run latency and retained state on `ping3`

Chromium 145 at 1500 × 900 and 4× CPU throttling ran the same 20 verified
mouse selections per batch. The original mounted 82,529 Full Text rows;
`ping3-reused-paint-styles.html` was measured separately with all 82,529 rows
and with 160 Windowed Text rows. The script collected garbage before the
run and at quarter points, outside the timed clicks. Early means batches
2–4; late means the last three batches. This compares complete report
versions, not the isolated effect of paint-style reuse.

| Report and Text mode | Clicks | First mouseup handler | Early → late median handler | Average style time per click |
| --- | ---: | ---: | ---: | ---: |
| Original, Full Text | 240 | 1,073 ms | 217.8 → 233.9 ms | 219.9 ms |
| Newer, Full Text | 480 | 59.3 ms | 3.2 → 2.8 ms | 2.1 ms |
| Newer, Windowed Text | 480 | 54.3 ms | 3.0 → 2.9 ms | 2.0 ms |

In the newer report, from the first GC checkpoint (120 clicks) to the
last (480), node counts stayed within two nodes in Full Text (2,584,479
→ 2,584,477) and were unchanged in Windowed Text (7,146). Listener counts
were flat; post-GC JS heap grew by 0.26 and 0.24 MiB, respectively. A
second Windowed Text run forced GC only at the end; early and late
two-frame medians were 30.1 and 30.2 ms, and the final node and listener
counts matched the checkpoint run. This does not reproduce a runaway
slowdown or accumulating DOM/listeners for this fixed click sequence. It
does not rule out longer-term retention, novel slice sequences, or other
interactions. V8 heap and DOM node counts are separate metrics, not total
browser memory.

The two-frame proxy still exceeded 70 ms on the newer report's cold first
click. A trace attributed a 30.7 ms layout to the first split-size read
after mounting the detail DOM. Measuring the split limit before mounting
reduced the median layout count from four to three, but did not improve the
two-frame median in seven alternating pairs per mode (75.1 → 76.6 ms
without visiting Text; 71.1 → 72.3 ms after visiting Windowed Text). The
split experiment is not enabled.

To reproduce, use the indices from the sustained selection command above.
For the newer report use `--batches 24 --gc-every 6`, with `--text full
--rows 82529` or `--text windowed --rows 160`; for the original use
`--batches 12 --gc-every 3 --text full --rows 82529`. Omit `--gc-every`
to collect only at the end. Raw runs and the first-open trace are under
`build/benchmarks/20261120-sustained-ping3/`.

Two further first-open probes alternated seven fresh pages per variant
in Chromium at 4× CPU throttling, clicking slice 665 at 1500 × 900.
Warming the detail fonts before the click yielded a 69.9 ms median
two-frame proxy, and applying `contain: layout` to the detail body
yielded 71.1 ms, versus 69.3 ms for the control. Neither experiment
is enabled. Logs: `build/benchmarks/20261120-detail-first-layout/isolated.jsonl`.

Rechecking the `ping3-batched-btf.html` Windowed Text report on the same
Chromium version, viewport and 4× throttle, 160 real clicks on 20 distinct
slices did not reproduce progressively slower selection. From the first to
second forced-GC checkpoint (80 → 160 clicks), nodes stayed at 7,155,
listeners at 170, and V8 heap changed from 61.22 to 61.30 MiB. The first
click remained the outlier (61.2 ms handler; 92.2 ms to the second animation
frame in this run).

With the chart-focus fix, repeating the same sequence kept the same node and
listener counts at both GC checkpoints. Across the 140 clicks after the first
batch, median handler / two-frame time was 3.4 / 28.7 ms before and 2.9 / 30.0 ms
after. These single runs check for a repeat slowdown, not a latency improvement.

We also tested size containment on the split pane, a zero flex basis on the
scrolling track area, and skipping the detail body during the first geometry
read. Seven alternating pairs per variant retained the same pane geometry;
none improved the median two-frame proxy (77.3 → 77.6 ms, 79.1 → 81.6 ms,
and 76.4 → 76.5 ms respectively). None of these layout changes is enabled.
Logs: `build/benchmarks/20261120-selection-refresh/`.

### Defer call stack rows in a short desktop split

The stack starts collapsed when a newly selected call opens in a very short
split, even in a wide desktop window. Resizing leaves an existing stack open.
**Actions** exposes the section shortcuts; **Export stack CSV** still works
without expanding the stack.

On `ping3`, Chromium 145 with a 1500 × 900 viewport, 4× CPU throttling and a
108 px split (`--detail-fraction 0.12`) ran two 100-click runs per version in
before/after/after/before order. Excluding the first batch, the median stack
went from seven rows and 67 descendant elements to zero rows and seven
elements. Median handler time was 3.0–3.5 ms before and 2.4–2.8 ms after; the
two-frame proxy overlapped at about 28–30 ms. This reduces eager DOM work in
short splits, not established first-open latency in the default layout.
Logs: `build/benchmarks/20261120-first-detail-trace/`.

### Keep context lists at 100 rows per page

Stack, error, inline and synthetic-child lists now replace each page instead
of retaining previously displayed rows. A deep stack indexes visible ordinals
by recorded frame on the first page change; subsequent pages seek directly
even when they start inside an untraced gap or inline chain. CSV still walks
the complete context, independently of the page.

A 20,000-child synthetic context inside the offline viewer ran two 40-page
sequences per version, in before/after/after/before order (Chromium 145,
1500 × 900, 4× CPU throttle). Mounted context rows at the end fell from 4,100
to 100, and post-GC browser node counts from 43,074 to 3,081. Median time
from programmatic button activation to two animation frames, excluding the
first five flips, fell from 333–352 ms to 33 ms. The new synchronous handler
is longer (17.5 vs 2.8–2.9 ms) because it also lays out and reveals the page
controls. This measures navigation deep into a wide synthetic context, not
default first-slice latency. Logs: `build/benchmarks/20261120-context-paging/`.

The pager now reveals its controls before mounting replacement rows, so the
click handler doesn't force layout of the new rows. **Current frame** is a
separate path that opens the relevant page and reveals the selected row.
Repeating the 40-page synthetic workload in alternating order, the median
ordinary paging handler fell from 19.4–20.7 ms to 3.9–4.3 ms. The two-frame
proxy stayed near 33 ms; total layout time did not decrease. Each flip kept
100 mounted rows and its focused pager visible. Logs:
`build/benchmarks/20261120-context-current-frame/`.

### Batch large context CSV exports

At 4,096 rows, use the existing Timeline work queue to serialize the full
iterator in 128-row batches. Progress and **Cancel CSV** are in the section
header. The job pauses while another view is active, survives paging and an
Overhead toggle on the same call, and is discarded if the detail closes or
changes selection. It uses the duration/self arrays and basis captured at
activation. The first work is deferred to give the browser a paint opportunity.
The complete file still needs a Blob; this is not streaming to disk.

Three alternating runs per version in Chromium 145 at 4× CPU throttle used
the same real-report shell with synthetic untraced frames containing 20,000
and 150,000 children. CSV bytes matched in both workloads, including names
that exercise Unicode, quoting and spreadsheet-formula protection.

| Context rows | Median click handler | Median longest frame gap | Median CSV ready |
| --- | ---: | ---: | ---: |
| 20,000 | 217.4 → 3.0 ms | 219.5 → 21.5 ms | 217.3 → 256.3 ms |
| 150,000 | 1,488.1 → 2.8 ms | 1,495.6 → 41.8 ms | 1,488.0 → 1,599.1 ms |

No Long Tasks API entry (50 ms or longer) was recorded during the measured
after-export intervals. The click and frame metrics are responsiveness
proxies, not physical input-to-display latency or disk-write time. The
100,000-function Range CSV retained its 512-row batch size and its output
hash. Its median ready time was 126.1 ms before and 117.6 ms after in a
separate unthrottled run; this is a regression check, not a speedup claim.
Reproduce with `tests/benchmark_context_csv.py`; logs are in
`build/benchmarks/20261120-context-csv/`.

### Yield during expensive detail breakdowns

**Where the time went** tries the existing raw-time partition in the selection
handler with a 3 ms traversal budget, checked between batches of 4,096 visit
or unwind steps. Only unfinished traversals defer to the Timeline queue,
leaving the other call details usable. The work pauses outside Timeline and
is discarded on close or selection change. The interruption, wait and
overhead accounting, bounded cache, and synchronous Slow vs Fast analysis
retain their existing semantics.

Five alternating Chromium 145 runs per version at 4× CPU throttle primed the
call-tree index, then selected a previously unmeasured subtree. The final
bar and legend HTML matched in every run. Medians in milliseconds:

| Selected subtree | Selection handler | Two-frame proxy | Breakdown ready |
| --- | ---: | ---: | ---: |
| `ping3` execve, 11,224 descendants | 11.5 → 11.4 | 38.4 → 37.6 | 11.6 → 11.4 |
| Synthetic root, 450,001 descendants | 20.1 → 8.9 | 43.2 → 33.5 | 20.3 → 43.0 |

The real call stayed synchronous; the synthetic case showed the pending
state in every adaptive run. This measures programmatic selection and DOM
readiness, not physical input-to-display timing. Reproduce with
`tests/benchmark_detail_breakdown.py`; logs are in
`build/benchmarks/20261120-detail-breakdown/`.

### Build a large track's call index without blocking selection

The first selection used to drain the track-index builder synchronously to
label the child and sibling controls. Now navigation, breakdown, and child
preview share one builder per track. They advance it in batches, use only
the complete index, and resume from the same position if selection changes.
Leaving Timeline pauses the work. The details and Parent control do not wait;
the other navigation controls update in place once their targets are known.

Four alternating before/after pairs in Chromium 145 at 4× CPU throttling
selected a call without priming the index. Final breakdown bars, legend, and
navigation controls matched. Medians in milliseconds:

| Cold selection | Handler | Two-frame proxy | Navigation ready | Breakdown ready |
| --- | ---: | ---: | ---: | ---: |
| Real `ping3` execve, 58,539 calls across 18 tracks | 45.5 → 41.5 | 88.7 → 87.2 | 45.5 → 41.5 | 45.8 → 86.4 |
| Synthetic leaf on a 900,002-call track | 80.3 → 43.5 | 113.2 → 94.2 | 80.3 → 115.9 | 80.4 → 117.2 |

These are programmatic selection and DOM-readiness measurements. The large
track yields sooner, at the cost of waiting longer for the computed controls;
the real report shows little change in the two-frame proxy. Reproduce with
`tests/benchmark_detail_breakdown.py BEFORE AFTER --cold --leaves 900000 --rounds 4`.
Logs: `build/benchmarks/20261120-cooperative-call-index/cold-ab.jsonl`.

Four 120-click runs on the real `ping3` report used the same 20 slices and
mounted Windowed Text. Warm handler medians were 3.2–3.3 ms before and
3.3–3.7 ms after, with overlapping two-frame medians (28.9–29.5 ms before;
27.1–29.4 ms after). Node and listener counts did not grow between the
60-click and 120-click forced-GC checkpoints in either candidate run.
This checks for repeated-selection regressions, not a warm-path speedup.

### Use actual split height for the initial stack

The saved height is a preference. A crowded toolbar or notes strip can
leave less than 140 CSS pixels of detail content space even outside the
compact breakpoints. The initial recorded call now chooses its stack
presentation after the split is sized; it does not lay out a stack page
just to discard it. This does not change the pane heights or close a stack
that was already expanded before a resize.

In the real `ping3` report, the 760 × 490 desktop and 844 × 500 and 760 × 520
touch cases kept their 139, 132, and 104 px detail areas, respectively, but
mounted zero stack rows until expansion. Stack row text matched the baseline
after expansion; the taller 157 and 286 px cases still opened immediately.
In a separate nine-pair 1500 × 900 experiment, changing only the order of
split sizing and body insertion showed no consistent speedup (median
42.6 → 43.0 ms handler, 71.3 → 70.0 ms to two animation frames); that
experiment is not enabled. This fix corrects the compact behavior and
avoids unused row rendering; it does not claim a general first-open speedup.
The short landscape touch layout also drops the regular view's 288 px
height floor so the 44 px view tabs don't push the split past the bottom
of a 390 px viewport. The chart keeps its 160 px stage minimum.
Logs: `build/benchmarks/20261120-first-detail-isolation/`.

### Re-audit the first click after consecutive selections

In `ping3-wait-compact.html` (1500 × 900, Windowed Text, Chromium 145, 4× CPU
throttling), the first actual mouse selection took 54.5 ms in the handler
and 82.9 ms to two animation frames. Across six batches of 20 verified
selections, the second and last handler medians were 3.0 and 2.8 ms;
the corresponding two-frame medians were 29.3 and 30.0 ms. At the 60- and
120-click forced-GC checkpoints, the page had 7,149 nodes and 172 listeners;
retained V8 heap changed by about 0.11 MiB. This sequence did not reproduce
progressive slowdown, and does not establish behavior for arbitrary clicks.

Three separate first-click Chromium traces show one synchronous Layout event
of 12.9–26.0 ms inside the mouseup handler. Applying the final detail split
height before mounting the panel did not help in nine alternating pairs:
median handler 43.6 → 42.6 ms, two-frame proxy 72.0 → 73.6 ms. This candidate
is not enabled. Continue targeting layout work, rather than treating the
first-click pause and increasing retained state as the same problem.

`tests/benchmark_timeline_selection.py` now accepts `--trace-first PATH` to
save a Chromium trace for the first verified activation and emit its layout
and paint event durations. Run traced and untraced samples separately: tracing
can change timings, and two animation frames are not physical display latency.
For example, use `--indices 665,2366 --text windowed --batches 1 --trace-first
first-click.json` with the above report. Local logs:
`build/benchmarks/20261120-ping3-click-audit/`.

### Show the selected call before indexing a large track

For a track spanning at least 32,768 slice slots without a completed call
index, selection now mounts the recorded-call detail before starting the time
breakdown and child summary. Both use the existing after-paint work queue
and share the index builder with call navigation. Parent navigation remains
available immediately; child and sibling navigation wait for the index. Work
pauses in Text, resumes on return and cannot update a closed or replaced
detail. Small tracks and selections on indexed tracks keep their immediate
work budgets.

Seven alternating real mouse-click pairs on the same ping3 slice (665,
58,539 calls in its track, Windowed Text previously opened, 1500 × 900,
Chromium 145, 4× CPU throttling): median handler time fell from 47.5 to
39.9 ms and time to two animation frames from 79.9 to 69.9 ms. In a separate
120-click run on two visible calls, the second and last 20-click handler
medians were both 2.9 ms and the page retained 7,120 nodes and 172 listeners
at the 60- and 120-click forced-GC checkpoints. This does not eliminate the
synchronous layout in `maxDetailH()` or cover arbitrary click sequences.

`benchmark_detail_breakdown.py --cold --index 665 --leaves 900000` also
verified identical rendered breakdown and navigation controls after the
queued work finished. In five alternating probe-selection pairs at 4× CPU
throttling, the real call's breakdown was ready in a median 86.2 → 82.5 ms,
while navigation readiness moved from 44.7 to 81.2 ms. For a leaf in the
synthetic 900,002-call track, the median handler was 42.8 → 24.8 ms and
breakdown readiness was 110.8 → 107.5 ms. The tradeoff is that even an index
that would have finished synchronously is now delivered after the detail
becomes visible. Logs: `build/benchmarks/20261120-first-open-layout/`.

On a leaf, completing a deferred child summary now removes the temporary
section, matching the immediate path. If the waiting explorer had keyboard
focus, focus moves to the call's Actions button without scrolling. The
regression checks both the empty leaf and the indexed parent with children.

A follow-up tested `contain: layout` on the recorded-call detail pane in
nine alternating pairs under the same real-click setup. The handler median
was 39.3 → 41.2 ms and the two-frame median was 71.5 → 72.6 ms. The rule
did not improve first selection and is not enabled. Logs:
`build/benchmarks/20261120-first-open-layout/isolated-detail-ab.jsonl`.

A later test used six alternating rounds with the current `ping3` report and
the same desktop setup. Size-and-layout containment left the two-frame median
at 69.7 ms versus 71.0 ms without it. Allowing the browser to skip offscreen
call-stack layout shifted some work out of the click handler (39.4 → 34.6 ms),
but the second frame was still at 70.4 ms. Neither CSS experiment is enabled.
Logs: `build/benchmarks/cold-detail-layout.FCRpCs/desktop.log`.

### Keep vertical split limits stable with Notes visible

A CSS flex-shrink prototype reduced the median layout count across two real
clicks from eight to seven in nine alternating pairs. The first-click handler
median was 38.2 → 36.0 ms and the two-frame median was 66.0 → 64.6 ms. It is
not enabled: giving Notes a fixed share instead of letting them shrink left
as little as 64 px for details in some short viewports and could push controls
below the viewport. Logs: `flex-split-ab.jsonl` and `flex-geometry.jsonl` in
`build/benchmarks/20261120-first-open-layout/`.

The geometry comparison exposed a separate feedback bug: the divider's
maximum was based on the current, flexed height of Notes. Shrinking the
details let Notes grow, and `End` no longer returned to the previous maximum.
The limit now reserves the CSS minimum of visible in-flow Notes, not their
current height. Notes can still shrink and scroll; hidden or overlaid Notes
do not consume vertical split capacity. The regression covers standalone and
sidecar reports, `Home`/`End`, the separator's accessible value, and toggling
Notes without losing the minimum chart height.

### Skip redundant paints at vertical split limits

Moving the vertical divider farther past a limit no longer redraws the
track canvas when the clamped height, separator value and limit, chart
viewport, and device pixel ratio are unchanged. Reversing direction back
into the allowed range still resizes and repaints. The displayed split is
still saved once on release.

In three alternating real-mouse pairs on `ping3` (1500 × 900, Chromium 145,
4× CPU throttling), 24 moves beyond either the 60 px or 569 px limit caused
24 full track paints before and zero after in each pair. At the 60 px limit,
the median cumulative CDP `ScriptDuration` for those moves fell from
127.7 to 5.0 ms. This measures scripted work over 24 moves, not input-to-display
latency or the cost of selecting the first call. Log:
`build/benchmarks/20261120-first-open-layout/vertical-divider-ab.jsonl`.

### Apply divider geometry and paint in the same frame

The divider preview now runs at the start of the existing canvas animation
callback, before hover hit testing and drawing. Previously the divider's own
callback changed the CSS size and queued the canvas for the following frame.
The canvas was not blank in that interval, but the browser stretched pixels
from the previous split. The preview still keeps only the latest move and
is discarded if the user switches views before it runs.

In Chromium at DPR 2 and 4× CPU throttling, the real `ping3` report showed a
stale backing-store dimension in 8 of 8 first-frame samples on each input
path (vertical mouse drag at 1500 × 900 and horizontal touch drag at
844 × 390). Both fell to 0 of 8, with unchanged settled sizes and selection.
The regression also tests desktop geometry before the next paint in both
delivery modes, both themes, and at DPR 1, 1.25, and 2. Logs:
`build/benchmarks/20261120-first-open-layout/drag-same-frame-real.jsonl`
and `build/benchmarks/20261120-first-open-layout/drag-same-frame-touch.jsonl`.

### Reuse the open slice on a Text jump

Reselecting the same slice now preserves its existing detail nodes and
pending work, including on Text → Timeline jumps. The jump still frames and
reveals the call, and clears a stale dense-pixel notice when it no longer
applies. Selecting a different call, dismissing details, or replacing the
pane with an analysis still opens the appropriate detail normally.

Twelve Text → Timeline → Text round trips to line 948 on `ping3` at
600 × 850 caused twelve detail rebuilds before and zero after. The expanded
stack stayed open on all twelve trips after the change, and the Text reading
position was unchanged on both versions. This measures detail reconstruction,
not first-click or round-trip latency. Log:
`build/benchmarks/20261120-first-open-layout/reuse-jump-ab.jsonl`.

### Apply narrow value layout only to expanded evidence

The recorded-call pane now becomes an inline-size query container only while
an enum or recorded-value disclosure is open. This is the only content that
uses the pane-width query. The prototype does not need it. Closing the last
value disclosure or selecting another call restores the default layout;
expanded values still use the full detail column in a narrow pane.

In 12 alternating pairs on the real `ping3` trace (1500 × 900, Chromium 145,
4× CPU throttling, Windowed Text mounted), cumulative layout time over four
verified clicks dropped from a median 39.3 to 31.2 ms. The first click changed
from 44.3 to 43.5 ms in the handler and 74.3 to 73.1 ms to two animation
frames. Warm times overlapped. A trace still shows a synchronous layout on
the first click, so this change reduces layout work without eliminating the
initial pause. Making every detail a query container from the start showed
no benefit in the exploratory comparison.

A separate 120-click run alternated 20 distinct calls. The second and last
batches had median handler times of 3.2 and 3.0 ms, respectively. The 60- and
120-click forced-GC checkpoints had 7,149 DOM nodes and 172 listeners each.
This sequence did not reproduce progressive slowdown. Reproduce with
`tests/benchmark_timeline_selection.py --text windowed --indices 665,2366`
for the two-call comparison, or the 20 indices listed above for the broader
sequence. Logs: `build/benchmarks/20261120-selection-render/`.

### Size the initial vertical detail and mount its stack together

When a recorded call opens a closed vertical split outside compact mode,
read the available height with the pane still closed. Mount the call stack
in its initial open or collapsed state before showing the pane, so the same
layout can size the split and its contents. If the measured content height
disagrees with the predicted short-pane state (for example, because of
scrollbars or a border change), correct the stack before revealing the call.
Compact and landscape layout behavior is unchanged.

In 14 alternating pairs on `ping3` at 1500 × 900 (Chromium 145 with 4× CPU
throttling and Windowed Text mounted), the median layout count across two
verified clicks fell from seven to five. The first handler median was
36.6 → 35.7 ms and the two-frame proxy was 63.4 → 62.1 ms; cumulative
layout time was 19.4 → 19.9 ms. This reduces repeat layout passes, not the
cost of laying out the pane itself. Presizing without mounting the stack
together did not improve first-click latency. `content-visibility: auto`
on the detail columns shortened the handler but shifted layout to the next
frame without improving the two-frame proxy, so it is not enabled.
Logs: `build/benchmarks/20261120-selection-latency/`.

### Reuse visible track header text and metrics

CPU lanes reuse their measured label width for both the gutter and the
segment text inset. Track headers reuse their titles and subtitles; the
subtitle still follows filter hit counts and the CPU expand/collapse hint.
When a header scrolls entirely above the canvas, its text, marker strip and
background are not drawn. Partially visible headers keep normal rendering.

In an eight-frame real `ping3` pan after the six CPU labels had been seen,
CPU label measurements fell from 96 to zero and track title measurements
from 16 to zero. At a 600-pixel vertical scroll, eight fully offscreen titles
were no longer drawn. Before/after track canvases matched pixel for pixel
for all 24 frames at each of DPR 1 and 1.25. These are counts of avoided work,
not a claim about overall frame rate. Logs:
`build/benchmarks/20261120-track-header-reuse/`.

### Reuse CPU run paint state within a lane

Consecutive runs on the same task reuse its resolved color and filter
opacity. Canvas fill and opacity are assigned only when they change;
drawing a label switches the fill back before the next run. Run order,
rectangles, hover targets, observation outlines and IRQ strips are unchanged.

At DPR 1 and 1.25, eight frames each of unfiltered, filtered and dark
rendering matched pixel for pixel before and after, on both real `ping3`
and a synthetic lane with 60,000 runs. In the synthetic four-runs-per-task
sequence, unfiltered opacity writes fell from 471,782 to 24 across eight
frames, and fill-style writes from 472,014 to 118,196. Six alternating
timing pairs without setter hooks reduced the median unfiltered CPU paint
from 16.1 to 14.4 ms. With a task switch on every run, timings overlapped;
on the real `ping3` trace, CPU paint was already around 0.1 ms. These
measurements cover CPU lane painting, not overall timeline interaction.
Logs: `build/benchmarks/20261120-cpu-lane-paint/`.

### Compare the original and current reports at the same Text row count

On Chromium 145 at 1500 × 900 with 4× CPU throttling, six batches
repeated the same 20 verified mouse selections in the original `ping3.html`
and in `ping3-cpu-lane-paint.html`. In the last 60 clicks, style recalculation
averaged 228.3 ms per click in the original, against 2.2 ms in the current
report with the same 82,529 Text rows. The old handler median rose from
223.9 ms in batch two to 251.0 ms in batch six; the current Full Text run
was 3.2 → 3.1 ms. With 160 Windowed Text rows the current run was
3.3 → 3.0 ms. Nodes and listeners were unchanged at the 60- and 120-click
forced-GC checkpoints in all three runs. This compares report versions and
does not attribute the improvement to one patch or rule out other sequences.

The current report still had a cold first click. `--trace-first` now emits
the available JavaScript stack for each Layout event and identifies whether
it happened in the handler or afterward. A desktop trace showed 27.9 ms in
`updateShortDetail` during `openDetail`'s split resize. At 390 × 844 with
touch, presizing the vertical pane before mounting compact detail removed
one handler Layout in a traced run, but eight untraced alternating pairs
showed overlapping handler (26.5 → 26.2 ms) and two-frame (39.3 → 39.1 ms)
medians. This compact candidate is not enabled. Raw logs:
`build/benchmarks/20261120-click-layout-followup/`.

Replacing the recorded-call body grid with two flex columns also showed
overlapping first-click timings in ten alternating desktop pairs on the
same report with Windowed Text. The two-frame median moved from 69.5 to
67.0 ms, while the median layout count over two clicks rose from five to
six. The grid is retained. Logs: `build/benchmarks/20261120-detail-columns/`.

### Check the layout scope after the Text return changes

On `ping3-sticky-task-return.html` with all 82,529 Text rows mounted, Chromium
145 at 1500 × 900 and 4× CPU throttling completed 120 verified slice clicks.
The handler median was 3.2 ms in both the second and last 20-click batches.
At the 60- and 120-click forced-GC checkpoints, DOM nodes and event listener
counts matched; retained V8 heap differed by about 0.09 MiB. This sequence
does not reproduce progressive slowdown; it does not rule it out elsewhere.

With Windowed Text, a separate first-click trace reported a 29.7 ms non-partial
Layout rooted at `#document` in `updateShortDetail`, with 274 dirty layout
objects out of 353 in Chromium's layout counters for that event. These counts
are not the report's DOM node count. `--trace-first` includes these counters,
the root names and the partial-layout flag when present in the browser trace.

Applying `contain: layout` or `contain: layout size` only to the open detail
pane did not help consistently. In seven rotated runs per variant (untraced,
with Windowed Text previously opened), the first-click handler medians were
35.5, 35.8 and 35.0 ms respectively for baseline, layout-only and layout-plus-size;
the two-frame medians were 64.6, 64.1 and 63.6 ms. The candidate measurements
overlapped the baseline, so neither rule is enabled. Logs:
`build/benchmarks/20261120-sticky-return-clicks/`.

A subsequent seven-pair test applied layout containment to the entire Timeline
instead of the detail pane. The first-click handler and two-frame medians changed
from 36.3 / 66.7 ms to 36.6 / 65.3 ms, with overlapping samples. The rule is not
enabled. Logs: `build/benchmarks/20261120-split-layout-scope/root-contain-ab.jsonl`.

### Release closed detail contents

Closing a Timeline detail now detaches its contents and stops observing the old
header after cancelling source and analysis work. Switching to Text without
closing the detail still preserves the current panel and its DOM.

On the standalone `ping3` report at 1500 × 900, closing the test call, Tasks,
and Errors left 163, 293, and 202 descendant elements in the old panel. The
updated report left zero for each. The same close, reopen, and focus checks
passed at 844 × 390 with touch emulation. This measures retained DOM after
closing, not a first-click latency reduction. Logs:
`build/benchmarks/20261120-split-layout-scope/close-dom-ab.jsonl`.

### Skip the detail reset when opening at the scroll origin

In matched standalone `ping3` reports at 1500 × 900 desktop and 844 × 390
touch, opening the pane for the first time, closing and reopening at the origin,
and closing and reopening after scrolling made three explicit resets before
and one after on each viewport. All three ended at the origin in both reports.
The test measures `scrollTo` calls, not click latency. See
`build/benchmarks/current-ping3-selection.kbmVOa/reset-ab.jsonl`.

A separate desktop run on the pre-change report (Windowed Text, Chromium 145,
4× CPU throttling) selected the same 20 confirmed slices over six batches. The
handler median was 3.1 ms in batch two and 2.9 ms in batch six. The 60- and
120-click forced-GC checkpoints both had 7,153 DOM nodes and 173 event
listeners. A trace measured 24.1 ms in the synchronous first-click layout;
repeated clicks in this run did not reproduce progressive slowdown. See
`build/benchmarks/current-ping3-selection.kbmVOa/desktop.jsonl`.

### Reuse formatting for repeated recorded arguments

The plain ftrace argument renderer reuses escaped markup by report string ID.
It keeps up to 128 entries, each with at most 512 input and 2,048 output UTF-16
code units. The cache does not hold DOM nodes or affect BTF aliases, typed slots,
or recorded parameter names. Two IDs that share a slot never share their markup.

In 11 alternating Chromium 145 pairs, formatting the 47,749 applicable
argument records from `ping3` in capture order took 110.0 → 73.6 ms (median),
with 47,749 → 23,021 boundary scans and identical HTML for every record. An
all-distinct replay of the 14,385 inputs took 51.4 → 52.6 ms. The comparison
covers argument formatting only, not DOM mounting or click latency. See
`build/benchmarks/detail-argument-reuse.qVDRrW/ping3-fixed-cache-ab.json`.

The argument splitter now handles values without quotes or grouping delimiters
with a direct comma split. Nested values, quoted strings, and unmatched closers
still use the original parser. Of 14,917 distinct argument strings referenced
by the same `ping3` Timeline, 12,644 take the fast path. In 13 alternating
Chromium 145 pairs, the plain formatting replay over all 50,063 references
took 82.5 → 68.9 ms (median) with the bounded cache enabled on both sides;
the all-distinct replay took 55.8 → 46.0 ms. Every distinct input produced the
same split and escaped HTML. These replays exercise the plain formatter even
for input that may use typed rendering during real interaction; they do not
measure saved milliseconds on a single click. Log:
`build/benchmarks/ping3-first-selection/argument-split.vdyFLD/ab.json`.

### Verify the integrated offline preview

The rebuilt `ping3-interaction-preview-20261120.html` contains 58,539 slices
and 82,529 Text records. Decoded Timeline columns, strings and BTF detail
match the preceding BTF preview. On 1500 × 900 desktop and 844 × 390 touch,
line 4723 showed the verified `HK_TYPE_DOMAIN` label, the prototype started
collapsed, and both trace-jump return paths restored the previous position.

With all 82,529 Text rows mounted and 4× CPU throttling, a 40-click browser
comparison against the original `ping3.html` measured a second-batch median
of 224.3 → 3.2 ms in the handler and 242.8 → 23.1 ms to two animation frames.
The new report's separate Windowed Text run completed 120 clicks; the 60- and
120-click forced-GC samples both had 7,159 DOM nodes and 173 event listeners.
This is a comparison of full report builds, not an attribution to any one
change. The original report was left intact. Build, smoke check and benchmark
logs: `build/benchmarks/ping3-integrated-preview.m0nxec/`.

### Set the initial landscape split before measuring its contents

When a recorded call opens a closed touch-landscape detail, the stage still
occupies the full split width. Calculate the pane width and limits from that
width and the divider's CSS width, then mount the pane at its final size.
Subsequent resizes still use the measured split. The saved proportion, limits,
and vertical layouts are unchanged.

On `ping3` at 844 × 390 with Windowed Text mounted and 4× CPU throttling,
all four traced before/after pairs lost the synchronous layout in
`availableDetailWidth` on the first tap. Across 12 untraced alternating pairs,
median first-tap handler time was 28.4 → 27.2 ms and the two-frame proxy was
40.8 → 40.2 ms; individual timings overlap. The reproducible result is one
fewer forced layout in the traced handler, not a guaranteed latency win.

A separate 120-tap run on the pre-change preview cycled ten verified slices.
Handler medians for the second and final batches were 2.4 and 2.2 ms, with
7,044 DOM nodes and 173 listeners at both forced-GC checkpoints. This sequence
did not reproduce progressive slowdown. Reproduce with
`--viewport 844x390 --touch --scroll-top 235 --text windowed --batches 12 --gc-every 6`
and indices `40710,40680,40683,40685,40711,40712,40713,40716,40718,47465`.
Logs: `build/benchmarks/current-ping3-touch-audit/`.

### Reuse decoded ftrace enum arguments between hover and details

The tooltip and recorded-call detail share a 128-slot cache for native
ftrace enum argument parsing. An entry matches only the same interned
argument record and the same BTF signature; a slot collision replaces the
entry. Records over 512 UTF-16 code units bypass the cache. Nothing caches
DOM, and uftrace keeps its per-record checks and positional decoding.

In 17 alternating Chromium 145 pairs at 4× CPU throttling, replaying the
2,314 `ping3` records with enum parameter metadata required 2,314 → 808
argument boundary scans, with equal parsed results. Median parsing time was
19.8 → 6.5 ms. Replaying just the 535 distinct signature/record pairs did
not reduce scans (4.1 → 4.5 ms). This measures parsing in capture order,
not click or paint latency. Logs: `build/benchmarks/ping3-enum-eligibility/paired.json`.

### Keep the call panel responsive when it contains many off-CPU intervals

The call's time breakdown now uses the same indexed interval blocks as the
range summary and yields after each batch of approximately 8,192 candidates.
The selected call and its controls appear first. An unfinished breakdown pauses
in Text and is discarded if a different call replaces it. The final breakdown
still uses the exact recorded overlaps, wait categories and stack depths.

Five interleaved pairs in Chromium 145 at 4× CPU throttling, with the call-tree
index already built, compared the real `ping3` slice 665 and a synthetic call
spanning 600,000 off-CPU intervals. Median times in ms:

| Case | Selection handler | Maximum observed frame gap | Breakdown ready |
| --- | ---: | ---: | ---: |
| Real `ping3` | 16.3 → 16.9 | 33.2 → 33.4 | 16.3 → 16.9 |
| 600,000 intervals | 31.6 → 10.5 | 77.3 → 53.0 | 31.8 → 93.6 |

The completed bars, legend and navigation controls matched in both cases. The
synthetic breakdown finishes later, but no longer blocks the whole selection;
the frame-gap measurement is not display latency. A separate 60-click audit
of the real preview with Windowed Text found stable batch medians (3.2, 3.1,
3.2 ms, rounded) and 7,153 nodes and 173 listeners at each post-GC checkpoint.
That sequence did not reproduce progressive slowdown. Reproduce the breakdown
comparison with `tests/benchmark_detail_breakdown.py before.html after.html
--index 665 --leaves 0 --waits 600000`. Logs:
`build/benchmarks/selection-source-revisit/`.

### Check retained state after repeated selection

The selection benchmark accepts `--retained-growth-limit NODES,LISTENERS`
with at least two completed `--gc-every` checkpoints. It compares each later
post-GC sample with the first completed batch checkpoint, not with the closed
pane at startup. Exceeding either allowance fails the run, even if the final
sample returns to baseline. The gate does not use heap size as a proxy for
node or listener retention.

On the windowed `ping3-text-window-projection-preview-20261121.html` at 4× CPU
throttling, both 120-activation runs passed with `--retained-growth-limit 0,0`.
Desktop (1500 × 900, 20 slices) retained 7,153 nodes and 173 listeners at both
60 and 120 clicks. Touch emulation (844 × 390, 10 slices) retained 7,044 nodes
and 173 listeners at both checkpoints. Use the existing desktop sequence
with `--text windowed --batches 6 --gc-every 3`; use the ten-call touch
sequence with `--batches 12 --gc-every 6` and its viewport and scroll options.

The first desktop activation still included a roughly 28 ms synchronous
layout in one trace. Measuring the empty split before inserting the call
instead moved that layout to `revealRow`, so that candidate was not kept.
Logs: `build/benchmarks/timeline-first-detail-next/`.

### Size the first desktop detail before mounting its body

When a chart click opens a closed, non-compact vertical call panel, mount the
header first and size the split without the call body. If the pane fits in the
viewport, reveal the selected row before mounting the body. Otherwise reveal
the row after the body is mounted, so page scrolling uses the final controls.
The body, stack, and actions are available by the end of the same handler;
short and landscape layouts keep their existing paths.

In 12 alternating pairs on `ping3` at 1500 × 900 in Chromium 145 with 4× CPU
throttling and Windowed Text mounted, median first-click handler time fell
from 36.7 to 22.9 ms. The two-frame proxy stayed comparable (64.8 → 65.9 ms).
The two-click layout count increased from 6 to 6.5 (median), and total layout
time stayed comparable (20.3 → 20.5 ms). One traced pair shifted the
approximately 28 ms forced layout to 4.5 ms in the handler and 20.2 ms in the
frame. This shortens the synchronous click handler; it does not demonstrate
faster display of the finished details. Logs:
`build/benchmarks/selection-detail-containment/`.

### Use the platform monospace stack in Timeline details

The detail title, recorded values, raw evidence and call context use the same
short font stack: the platform UI monospace face, then the browser's generic
monospace. Prose, tooltip fonts and Text are unchanged. This keeps code and
numbers in a fixed-width face without trying additional named fallback faces.

With the same offline `ping3` payload, 28 alternating desktop pairs across
three slices (1500 × 900, Chromium 145, 4× CPU throttling, Windowed Text
mounted) measured the following medians in milliseconds:

| First slice | Pairs | Layout time over two clicks | First click to two animation frames |
| --- | ---: | ---: | ---: |
| 665 | 12 | 28.8 → 22.9 | 82.5 → 77.8 |
| 2366 | 8 | 22.3 → 19.3 | 68.9 → 68.2 |
| 47209 | 8 | 23.4 → 20.0 | 72.7 → 68.0 |

Handler medians stayed within 1 ms per slice. The frame samples overlap and
are a timing proxy, not a display-latency measurement. All six before/after
pane screenshots matched exactly on this host, covering desktop, touch
portrait and touch landscape, light and dark themes, and expanded prototypes.
Logs: `build/benchmarks/selection-detail-cost/`.

### Mount the first desktop detail in one pass

On the first click in a desktop vertical split, measure the open, empty pane
before mounting the header and body together. If the pane fits on screen,
reveal the selected chart row without measuring its new header controls.
If the pane extends below the viewport, use the existing control-reveal path
after mounting. The details are still available in the same click handler.

In 12 alternating `ping3` pairs (1500 × 900, Chromium 145, 4× CPU throttling,
Windowed Text mounted), the median first-click handler took **22.6 → 18.8 ms**.
The median to two animation frames was **64.9 → 63.5 ms**, and total layout
time over two clicks was about **18.2 ms** in both runs. This demonstrates
less synchronous work, not faster on-screen paint. A separate 240-click
baseline run found no progressive slowdown: post-GC node counts were 7,153,
then 7,195 at each of the next three checkpoints, with 173 listeners throughout.
The freshly generated preview held 7,162 nodes and 174 listeners at all four
post-GC checkpoints over the same 240 selections.
Logs: `build/benchmarks/ping3-revisit-audit/`.

### Count context rows only when needed

A collapsed call stack still counts recorded ancestors for its title. It no
longer scans them again to count inline, untraced and outside-window rows
until the user opens the stack or exports it. The count is reused for that
selection. The expanded desktop stack still computes its total immediately.

In the offline `ping3` report at 844 × 390, selecting 22 calls with the stack
collapsed reduced reads of the caller source table from 410 to 22. The stack
labels, expanded rows and exported CSV bytes matched for all 22 calls. This is
a count of reads, not a latency measurement. A separate 120-tap run on the new
report kept 7,051 DOM nodes and 173 listeners at both post-GC checkpoints (60
and 120 taps). Logs: `build/benchmarks/selection-layer/`.

### Reuse the count pass for deep-stack page seeks

Counting context rows also records a cumulative row offset every 32 recorded
frames. Paging searches those offsets and checks at most 32 recorded frames to
find the start of a page. The collapsed stack still skips this work, and the
index uses one entry per completed block rather than one per recorded frame.
After the first seek, this trades a short local scan for a smaller index.

In a synthetic stack of 4,096 recorded frames, the first **Current** action
read the caller source table 4,287 times before and 192 times after. Opening
the stack read it 4,296 times in both versions. The final page still showed
rows 4,001–4,096 and focused the same recorded call. This demonstrates less
work on the first seek, not a measured latency improvement. Logs:
`build/benchmarks/stack-page-checkpoints/`.

### Recheck repeated selection with BTF value shortcuts

In `ping3` at 1500 × 900 (Chromium 145, 4× CPU throttling, 160 mounted
Windowed Text rows), 160 real clicks cycled through the same 20 slices. Batch
medians were 3.0 ms for clicks 21–40 and 3.3 ms for clicks 141–160. All four
post-GC checkpoints had 7,164 DOM nodes and 173 listeners. The first click
took 24.2 ms in its handler and one following layout pass took 21.7 ms.
This run did not reproduce progressive slowdown; it does not rule out growth
in other traces or interaction sequences. Log:
`build/benchmarks/btf-value-layout/selection-followup/repeat.jsonl`.

### Reuse chart font metrics during split changes

The track canvas measures the fixed monospace label width at its first paint
and again when the device pixel ratio changes. Changing only the canvas width
or height no longer measures the same text again. The backing store still
resizes immediately before painting.

On `ping3` at 1500 × 900, ten real divider moves produced ten different chart
heights at each of device pixel ratios 1 and 2. Both runs measured the label
width ten times before and zero times after, with identical canvas pixels for
all twenty corresponding frames. Twelve alternating first-click pairs also
had identical chart pixels; timing did not show a reliable latency change.
Logs: `build/benchmarks/btf-value-layout/canvas-font/`.

### Reuse normalized source links across call selections

Timeline details cache up to 128 URL validation results for the current
document base. Long inputs and resolved URLs bypass the cache; the allowed
protocols and the handling of invalid links are unchanged.

In a fresh Windowed Text report, 20 verified clicks on `ping3` produced
identical destinations, titles and visible text for all 184 inspected source
links. Calls to the URL parser from the detail link validator fell from 182 to
81. This counts repeated work, not a measured first-click latency gain. A
separate 80-click run measured no change in DOM nodes or listeners between
the first and last post-GC checkpoints.

The baseline first-click trace still showed about 19 ms in the first layout
after the handler. Sampling chart scroll before changing the canvas backing
store moved a later layout event but did not remove a pass. Applying size
containment to the canvases also kept the same number of layout passes in
these exploratory traces; neither canvas change is enabled. Logs and rebuilt
preview: `build/benchmarks/ping3-first-selection/`.

### Keep the track canvas at its device-pixel scale

The track renderer can fill the entire backing store in scaled coordinates.
It now sets the transform only after a backing-store resize, device-pixel
ratio change, or context restoration, instead of switching to identity and
back on each paint. The ruler still uses its existing renderer.

Twelve alternating `ping3` pairs (1500 × 900, Chromium 145, 4× CPU throttling)
with Windowed Text mounted replayed six real clicks per run at ratios 1, 1.25
and 2. The track canvas pixels matched for all 72 corresponding frames. Each
run made 12 → 1 track transform calls. Median time inside the first `render()`
fell from 11.1 to 7.4 ms, and the median sum over six paints fell from 30.7 to
26.1 ms. These measurements cover the track renderer, not frame presentation.

In 12 separate uninstrumented desktop pairs at ratio 1, the first-click
handler median was 16.7 → 17.2 ms and the two-frame median was 63.4 → 63.7 ms.
This does not show faster overall first-click feedback. In a prior Chrome
trace, the longest layout after `mouseup` completed before the animation-frame
callback that painted the chart, so the drawing change does not address that
layout cost. Logs: `build/benchmarks/ping3-first-selection/track-transform.zRPf2B/`.

### Reuse adjacent call-stack source lookups

The stack row iterator already looks at the next recorded frame to find the
call site inside the current frame. It now carries that frame's depth gap and
looked-up source chain forward to the next iteration. It still reads gapped
frames only when it reaches them, and it does not retain a cache for the whole
stack. Collapsed stacks still defer row counting; untraced and inline rows,
32-frame page checkpoints, and CSV scope are unchanged.

Real `ping3` checks (Chromium 145) compared desktop selection at 1500 × 900
and stack disclosure at 844 × 390. All eight comparisons matched both stack
row markup and CSV bytes. On the deepest recorded path (46 recorded frames,
63 displayed rows), caller source table reads fell from 251 to 81 on desktop
selection, from 250 to 80 on compact disclosure, and from 182 to 46 when
exporting the already-open stack. These are work counts, not latency savings.

Eight alternating pairs for the shallow first click (slice 665, Windowed Text
mounted, desktop, 4× CPU throttle) measured handler medians of 19.52 → 20.08 ms,
layout medians of 13.99 → 12.99 ms, and two-frame medians of 68.08 → 67.49 ms.
There was no clear first-click latency improvement. Earlier trials that moved
selection from `mouseup` to `click`, added inline-size containment, or fixed
context column widths likewise did not show a stable improvement and were not
adopted. The old handler marker was not comparable after moving the handler
to `click`. A separate 80-click run kept the same post-GC DOM node and
listener counts across all four checkpoints. Its median two-frame times
ranged from 29.4 to 30.1 ms across the four batches.
Logs: `build/benchmarks/ping3-first-selection/stack-lookahead.zxDjMB/`
and `build/benchmarks/ping3-first-selection/layout-cycle.8LTdZU/`.

### Set the recorded-call scroll inset on the pane itself

The sticky call header reserves scroll space using the pane's own
`scroll-padding-block-start`. Its measured height no longer changes an
inherited custom property throughout the recorded-call subtree. In short
panes there is no inset; analysis views still set the inherited offset for
sticky table headers. A browser test checks `scrollIntoView` below the call
header, resizing to a short pane, and switching to a range table in both
standalone and sidecar delivery.

Ten alternating traced `ping3` pairs (Chromium 145, 1500 × 900, 4× CPU
throttle, Windowed Text mounted, slice 665) dropped one Layout event in
every pair. Median style-tree update time was 11.22 → 8.48 ms and median
layout time was 16.29 → 14.76 ms. The removed layout was after the click
handler; the synchronous handler did not get faster.

Without Chromium tracing, the median from first click to two animation
frames moved from 64.90 → 62.45 ms for slice 665 (10 pairs), 58.25 → 53.60 ms
for slice 2366 (6 pairs), and 57.70 → 56.00 ms for slice 47209 (6 pairs).
The frame marker is not a screen-presentation timestamp. Logs:
`build/benchmarks/ping3-first-selection/detail-scroll-padding.LhR6zi/`.

### Restore the time ruler after a canvas reset

When the visible ruler context is restored, redraw only the ruler. If its
cached backdrop context is restored, invalidate the backdrop so the next
ruler paint rebuilds both overview and axis. Restoring either context while
the charts are hidden does not paint until the Timeline is visible again.
Browser regressions reset the backing stores and dispatch `contextrestored`;
they check exact pixels, selection, and that the tracks do not repaint.

On `ping3`, an experimental scaled bitmap copy matched the baseline pixels at
device-pixel ratios 1, 1.1, 1.25, 1.33 and 2, including selection, zoom,
theme changes, resize and restoring the detail split. In reversed-order runs
at ratios 1 and 1.25 it eliminated two transform calls per ruler paint, but
median ruler-call time stayed near 0.01 ms and the two-animation-frame marker
stayed near 33.3 ms. The frame marker is not presentation latency. The original
pixel-aligned copy is retained. Logs and replay:
`build/benchmarks/timeline-paint-verification/`.

### Keep selection diagnostics inside the detail pane

The repeated-selection benchmark now looks up stack and source diagnostics
inside the Timeline detail pane, not in the whole document. In a six-call
alternating-scope check on the 82,529-row `ping3` Full Text report, both scopes
found identical elements (including a 22-line source preview on one call).
The document-wide lookup took 139–146 ms at 4× CPU throttling; the pane lookup
was below the timer's resolution. The old probe could run in the microtask
after the second animation-frame marker, creating its own long task or delaying
the next click. This is a measurement fix; it does not change viewer rendering.

With the scoped probe, original `ping3.html` still overlapped 120 long tasks
with 120 verified clicks (230.1 ms warm handler median). The November 21
range-page preview, using the same sequence in Chromium 145 at 1500 × 900
and 4× CPU throttling, recorded no overlapping long tasks in either Text
mode. Full Text (120 clicks) had a warm handler median of 3.1 ms and retained
exactly 2,585,617 nodes and 174 listeners at the 40-, 80- and 120-click GC
checkpoints. Windowed Text (240 clicks, 160 mounted rows) had a warm handler
median of 3.0 ms and retained 7,203 nodes and 179 listeners at all four
checkpoints. Both Full Text runs mounted 82,529 rows. The preview's first-click
two-frame proxy still took 68–74 ms; this is not a screen-presentation
measurement. Warm medians exclude only the first click. This compares
complete report versions, not the effect of an isolated viewer change.

Two additional calls that required automatic track scrolling (one in each
direction, 1500 × 620) produced one scroll event followed by one track paint
each. The tested jumps did not justify changing the scroll invalidation path.
Logs: `build/benchmarks/20261121-repeated-selection/`.

### Size the first desktop split before a search jump mounts the pane

The first search jump in a regular vertical split can reuse the first-click
path: size the empty pane, reveal the selected chart row against that final
split, then mount the detail content. Previously, measuring the new split
after mounting forced layout of the call details inside the jump. There is
no timeout or loading placeholder. Short panes, landscape touch layouts,
and jumps that need to keep the toolbar or scroll the page still use their
existing positioning paths.

Ten alternating pairs on the same `ping3` report (Chromium 145, 1500 × 900,
first Enter on `^x2apic_send_IPI$`) produced these medians in milliseconds:

| Setup | Key to result and selection settled | Key to two animation frames after settlement |
| --- | ---: | ---: |
| Native CPU, Timeline opened directly | 10.9 → 7.1 | 24.1 → 23.0 |
| 4× CPU throttle, Windowed Text previously opened | 54.5 → 33.8 | 99.4 → 92.2 |

The frame metric is a browser timing proxy, not presentation latency. Native
layout counts increased from a median of three to five; the improvement is
in earlier synchronous feedback rather than a reduction in layout passes.
Paired desktop and touch checks kept the same zoom, chart and detail sizes,
scroll position and hittable selected row, including Shift+Enter to the last
match. The 844 × 390 landscape control showed no timing improvement. Logs:
`build/benchmarks/20261121-first-detail-phases/`.

### Size the landscape split before mounting the first call

Opening a closed touch-landscape pane from a tap or a search jump now applies
the saved horizontal split and measures the chart before inserting call
details. If the pane fits in the viewport, it can also position the selected
row before insertion. If the pane extends below the viewport, the existing
post-mount reveal still accounts for the visible search and detail controls.
The split limits, saved proportions, source loading, and return behavior are
unchanged.

On `ping3` in Chromium 145 with 4× CPU throttling, alternating before/after
pairs measured these median milliseconds:

| Setup | Pairs | To selection settled | To two animation frames |
| --- | ---: | ---: | ---: |
| 844 × 390, tap slice 40710, Windowed Text mounted | 10 | 24.9 → 14.8 | 38.4 → 37.8 |
| 844 × 390, search first match, Windowed Text mounted | 10 | 34.3 → 27.2 | 62.5 → 62.6 |
| 667 × 350, search last match with Shift+Enter | 6 | 37.4 → 25.8 | 60.2 → 53.5 |

Tap timing starts at the click event generated by the touch input, not at
touch-down. Search timing includes building the cold index. The two-frame
proxy does not show a consistent presentation improvement. Each pair picked
the same call with identical zoom, split size, and chart scroll position;
the selected row remained hittable. The three tap detail controls remained
hittable. Logs: `build/benchmarks/20261121-landscape-detail/`.

### Do not anchor the chart scroll position to its resizing canvas

The chart scroll container opts out of browser scroll anchoring. Its sticky
canvas changes height when the split or full-screen size changes; anchoring
to the canvas or its spacer could move the virtual row offset by the change
in height. The Timeline's explicit row positioning and normal scroll bounds
still apply. Text and detail pane scrolling are unchanged.

In Chromium, after **In Timeline** jumped to call 103 in `ping3`, entering
page full screen at 844 × 390 incorrectly moved the chart scroll offset
from 173 to 273 px and hid the selected row behind the ruler. The offset now
stays at 173 px, with the same zoom and selection. Seven of eleven viewport
sizes in the same sweep hid that row before; all eleven kept it hittable
afterward. Browser regression coverage includes page and native full screen
and returning to Text. Logs: `build/benchmarks/20261121-short-toolbar/`.

### Attribute first-click layout and long tasks separately

The selection benchmark now associates each long task with the batch and
slice it overlaps, and records its start relative to the click handler.
With `--trace-first`, it splits same-renderer-thread events into handler and
post-handler phases, including style-update stacks, animation callbacks and
timers. These durations can overlap: a callback can include a layout, so do
not sum categories as exclusive time. Two animation frames are still a
browser scheduling proxy, not display latency.

On the `ping3` keyboard-roundtrip preview, the single reported 50 ms long
task in the untraced 80-click Full Text run began 2.6 ms before the first
click handler (slice 665); it did not overlap later clicks. An instrumented
Windowed Text run (three fresh pages, 4× CPU slowdown, Chromium 145,
1500 × 900) spent about 9 ms drawing tracks, 3–4 ms resizing the canvas
backing stores, and under 1 ms painting the ruler during the first two
frames. Chromium traces also captured over 20 ms of post-handler layout.
In one traced Full Text run a forced style update at `updateShortDetail`
took 41 ms, versus about 2 ms in the earlier trace with the same reported
element count (three); do not infer a full-Text style scan from the outlier.

Eight alternating Windowed Text runs per variant compared recording dense
row paints immediately, recording them on repeated draws as before, and not
recording them at all. Recording immediately moved the first two-frame
median from 61.8 to 59.6 ms, but the Timeline-open median increased from
370.1 to 377.9 ms. Removing recording offered no repeat-click gain. Neither
variant consistently improved the interaction, so the viewer is unchanged.
Logs and experiment: `build/benchmarks/20261121-first-click-stage-audit/`.

### Show time breakdown before indexing a recorded leaf's siblings

Native ftrace marks calls recorded on one line with the leaf flag. These
calls never enter the parser's parent stack. Recovered orphan returns can
adopt them, but cannot make them parents; uftrace reconstruction does not
set this flag. The detail pane uses that information to omit the temporary
child summary and calculate the time breakdown without scanning the call
tree. Blocked time, preempted time, and tracing overhead are still counted.
Unmarked calls keep the existing behavior. The parent target remains
available immediately and the sibling buttons still wait for their index.

On `ping3`, the selected leaf `do_raw_spin_unlock` (slice 2, Text L28)
belongs to a 58,539-slice track. Across eight alternating before/after pairs
in Chromium 145 (1500 × 900, 4× CPU throttling, cold index), the median
time to breakdown in the DOM fell from 76.6 to 17.3 ms. The selection
handler took 15.8 → 17.0 ms, and the two-frame scheduling proxy was similar
(76.9 → 75.5 ms). Final breakdown and navigation output matched. This is
earlier feedback for leaf details, not evidence of faster chart rendering.
Samples: `build/benchmarks/20261121-native-leaf-detail/breakdown-ab.jsonl`.

### Enable nearby sibling controls without indexing the whole track

For a recorded leaf on a cold track, scan up to 256 slice ordinals in each
direction. Use the same task and recorded parent as the full index. Finding
the track edge proves that direction has no sibling. If the limit is reached
without a target or an edge, keep that control pending and finish the existing
cooperative index. A known sibling stays enabled while the opposite direction
is pending; its button and focus survive the index update. If both directions
are known, do not start an index solely for this selection.

On `ping3` slice 2, eight alternating Chromium 145 pairs (1500 × 900,
4× CPU throttle) moved the median time to both sibling controls being ready
from 73.9 ms to 16.3 ms. The selection handler (16.3 → 16.2 ms) and
time-breakdown output were unchanged within measurement resolution. The
two-frame scheduling proxy was 74.5 → 66.3 ms for this one cold selection;
this does not establish a general chart rendering speedup. Final labels,
enabled states, and time-breakdown output matched. Samples:
`build/benchmarks/20261121-leaf-siblings/cold-ab.jsonl`.

### Share an after-paint budget for recorded-call details

Navigation, time breakdown, and child preview previously posted separate
turns after a paint, each with its own work budget. They now take turns in
one scheduler. A paint schedules at most one detail-work callback. The
callback checks a shared 6 ms budget at generator yield boundaries and
continues after another paint when needed. The budget is not a hard time
limit for an individual batch. Closing or replacing the panel still drops
its work; switching views pauses it until Timeline is visible again.

Alternating Chromium 145 runs used a 1500 × 900 viewport and 4× CPU
throttling. They measured from programmatic selection to animation-frame
observations of the pane, not to screen presentation. Final navigation
labels, breakdown legends and child counts matched. Median milliseconds:

| Case | Pairs | Two-frame proxy | Time to breakdown | Time to child summary |
| --- | ---: | ---: | ---: | ---: |
| `ping3`, slice 665 | 8 | 79.0 → 78.7 | 79.0 → 78.7 | 79.0 → 87.5 |
| Cold synthetic parent with 450,000 descendants | 6 | 67.0 → 51.8 | 114.5 → 155.2 | 82.9 → 106.0 |

This favors earlier frames under unusually heavy concurrent detail work;
it does not speed up the computations or ordinary `ping3` paints. The
regression also checks that three pending sections schedule only one
callback per frame, with unfinished work continuing in a later frame.
Samples: `build/benchmarks/20261121-detail-frame-budget/frame-budget-ab.jsonl`.

### Publish call navigation as soon as its index is ready

The shared scheduler now treats the cold call index as a prerequisite for
non-leaf breakdowns and child summaries. Previously either summary could
finish the index and run its own calculation before the navigation job
resumed and enabled the buttons. The navigation job now enables them before
starting the dependent calculations. Leaf breakdowns do not depend on the
index. Changing Overhead while the index is pending replaces the child job
without scanning the cold index in the click handler.

In eight alternating `ping3` pairs on slice 665 (Chromium 145, 1500 × 900,
4× CPU throttling), median navigation readiness changed from 94.5 to 80.6 ms
and the two-frame proxy stayed near 81 ms. In six pairs on the synthetic
450,000-descendant parent, navigation changed from 89.6 to 97.2 ms, while
the median breakdown time stayed near 147 ms. This fixes the order in which
controls become usable; it does not make index building itself faster.
The benchmark verified identical final navigation and breakdown output;
a deterministic browser test gives each frame a single batch and verifies
navigation becomes usable while both dependent sections are still pending.
Samples: `build/benchmarks/20261121-detail-dependencies/`.

### Enable nearby navigation before indexing non-leaf calls

Cold recorded calls now check up to 256 slice positions per direction for
siblings and the first child. On tracks where parents precede children, the
child check starts at the next slice; otherwise it starts at the track's
first slice so it cannot skip an earlier valid child. Results still require
the recorded task, parent, and depth to agree. Unresolved controls wait for
the existing cooperative index and update in place without moving focus.

Eight alternating real clicks per slice on `ping3` (Chromium 145, 1500 × 900,
4× CPU throttling, Windowed Text opened before Timeline) measured median time
from mouse release to the First child button being enabled in the DOM:
81.0 → 15.0 ms for slice 665 and 78.9 → 14.9 ms for slice 2366. On slice 665,
both siblings became ready during the click as well; on slice 2366, those
controls still waited for the index. Final navigation, breakdown and child
summary matched. This is earlier control availability, not faster canvas
presentation. Samples: `build/benchmarks/20261122-cold-call-nav/`.

### Skip source controls when there is no excerpt

For recorded calls, check the source index before building the detail markup.
When the index has no excerpt, omit the disclosure instead of inserting it
and removing it in the same click. Calls with excerpts still load them on
demand; inline frames keep their missing-excerpt message.

On a `ping3` build without source excerpts, the 20-click desktop sequence
mounted 82 provisional source elements before the change and none after it.
The ten-call landscape touch sequence mounted 43 → 0. Settled detail markup,
zoom, split size and track scroll matched for every selection. These are
counts of avoided DOM work, not measured click latency savings.

In separate stability runs on the revised report (Chromium 145, 4× CPU
throttling, Windowed Text mounted), 160 desktop clicks and 80 landscape taps
showed no growth in nodes or listeners between the halfway and final post-GC
checkpoints. Early and late warm p95 two-frame timings stayed near 31.2 ms on
desktop and 32.7 ms on touch. No long task overlapped the measured click
windows. Logs: `build/benchmarks/20261122-click-profile/`.

The same repeat-selection sequences also passed with embedded source excerpts
in the gzip Windowed Text report. At 4× CPU throttling in Chromium 145, 160
desktop clicks and 80 landscape taps had no retained node or listener growth
between the halfway and final post-GC checkpoints. Early → late warm p95
two-frame times were 31.0 → 31.1 ms on desktop and 33.3 → 32.3 ms on touch.
These samples check for drift in the tested sequences, not cold-start latency
or every trace. Logs: `build/benchmarks/20261122-source-clicks/`.

### Trace a warmed-up selection separately from the first click

The selection benchmark accepts `--trace-click NUMBER PATH` to trace one
verified activation, counted from 1 across batches. This also works with
touch taps. The first marks belong to the chosen activation, not an earlier
click. `--trace-first PATH` still traces activation 1.

```sh
python3 tests/benchmark_timeline_selection.py REPORT.html --text windowed \
  --indices 665,2366 --batches 3 --trace-click 5 warm-click.json
```

On the filter-help `ping3` preview at 4× CPU throttling in Chromium 145,
slice 665 had 22.2 ms of layout from mouse release through the second
animation-frame mark on its first activation and 3.0 ms when revisited on
activation 5 (1500 × 900). In this traced pair, layout in the handler was
0.1 ms and 0 ms, respectively. The larger first-click layout occurred in
the frame, not the handler. This is a comparison of two traced samples of
the same slice, not a presentation measurement or a viewer speedup.

Separate repeated-selection runs compared early and late warm p95
two-frame times: desktop Windowed Text (160 clicks) 31.4 → 30.9 ms,
desktop Full Text (160 clicks, 82,529 Text rows) 31.3 → 31.5 ms, and
landscape touch Windowed Text (80 taps) 32.5 → 32.5 ms. None gained nodes
or event listeners between halfway and final forced-GC checkpoints. These
runs do not reproduce progressive slowdown; they do not exclude it on
other reports or interaction sequences. Logs and Chromium traces:
`build/benchmarks/20261122-timeline-warm-layout/`.

### Separate chart resizing from recorded-call body layout

Eight rotated runs per variant isolated the first click on `ping3` slice
665 (1500 × 900, Chromium 145 at 4× CPU throttling, Windowed Text mounted).
Temporarily hiding the facts column, context column, or entire recorded-call
body with CSS kept the selected call, chart size, split size and track scroll
identical. Median cumulative layout through the two-frame sample was 12.5 ms
with both columns, 8.6 ms without facts, 7.1 ms without context and 2.9 ms
without the body. All four variants recorded a median of three layout passes.
This experiment localizes work; hiding recorded information is not a fix.

Top-aligning the columns, applying inline-size containment to the two
columns, and combining both rules did not show a reliable improvement in
the two-frame proxy (56.9 ms for control; 58.4, 56.6, and 56.1 ms,
respectively, in a separate set of eight rotated runs per variant).
None of these CSS rules is enabled. Samples and reproduction script:
`build/benchmarks/20261122-first-click-layout-scope/`.

### Measure warm detail rebuilds and skip unused child averages

Timing the detail handler separately from rendering on the standalone
`ping3` Windowed Text preview at 4× CPU throttling (Chromium 145) gave warm
medians of **0.6 / 2.0 ms** for markup generation / DOM replacement at
1500 × 900, and **0.4 / 1.6 ms** at 844 × 390 on touch. The separately traced
desktop click on slice 665 had **19.2 ms** of frame layout on first open and
**3.1 ms** on revisit. These traces describe a sampled selection, not screen
presentation; the warm path does not justify caching whole detail DOM trees.

The child summary ranks by inclusive time, so building it no longer makes a
second pass over all child groups to calculate their averages. The full
explorer computes an average when it sorts by that column, renders a row or
exports CSV. In a synthetic cold preview with the call-tree index ready,
4,096 distinct groups needed **13 → 10** generator yields, and 65,536 groups
needed **253 → 190**. The top eight and counts matched in both cases. These
counts measure cooperative work, not a first-click latency improvement on
`ping3`. Logs and reproduction scripts: `build/benchmarks/20261122-detail-revisit/`.

### Leave resolved navigation controls unchanged

When the task's call index finishes, update only the attributes that changed
on the navigation buttons and their counterparts in Actions. Ready controls
keep their nodes, focus and accessible names. A browser regression that
resolves one pending sibling observed **8 → 2** attribute mutations; a first
click on `ping3` slice 2366 observed **8 → 4** with the same final labels and
enabled controls. Slice 665 resolved all navigation immediately and needed
no refresh in either version. Mutation counts do not measure layout time.

A separate first-click comparison tried layout containment on the body or
its columns, and replacing the outer grid with flex. Across eight rotated
runs per variant on each of slices 665 and 2366 (1500 × 900, Chromium 145,
4× CPU throttling, mounted Windowed Text), all variants kept the selected
call, chart, split, settled column geometry and text. None consistently
reduced layout time across both slices; the existing layout stays in place.
Logs: `build/benchmarks/20261122-detail-first-layout/`.

### Avoid recording paint runs for wide-only rows

After a row has been drawn with no subpixel groups, keep its starting position
for the current horizontal viewport and skip building the dense-paint command
array on repeat draws. This is capped at 1,024 rows per viewport, separately
from the dense cache's budget. Zoom and horizontal movement invalidate the
classification. Filters still affect the live drawing of each wide call.

In Chromium 145 at 4× CPU throttling, seven alternating pairs on `ping3`
kept the same track-canvas pixels on first selection and after zoom (desktop
slices 665 and 2366 and touch slice 40710). Zoomed desktop redraws staged
**20 → 0** and **2 → 0** paint commands respectively, with no consistent
first-click improvement. In a separate Node 24 test of 30 rows with 480
wide calls each, 500 draws took **101.6 → 47.3 ms** (median of 11 alternating
pairs). The Node test isolates row-loop bookkeeping with stubbed canvas calls;
it is not a measure of real paint latency. Logs, scripts and standalone preview:
`build/benchmarks/20261122-detail-cold-trace/`.

### Keep call navigation on one line on very narrow screens

At viewport widths up to 275 CSS pixels, the recorded-call navigation uses
smaller horizontal gaps and padding. It keeps the existing labels and 44 px
touch targets, and can still wrap when the text is enlarged. On `ping3`
at 240 and 270 px, the navigation went from two lines (101 px) to one line
(53 px). Touch checks covered parent/child navigation and the Text round trip
at five portrait and landscape sizes. Logs and the local preview are in
`build/benchmarks/20261122-short-call-reach/`.

### Restore the Timeline opener after a viewport resize

Returning from Text still leaves the Timeline's scroll position alone when
the original control is visible. If the viewport size changed and reflow
clipped the control, the return reveals it before restoring focus. Hidden,
disabled or detached controls still fall back to the existing focus target.
In seven touch checks of `ping3`, the original Actions button or trace-line
link was reachable and focused after return, compared with two of seven
before. Selection, time range and chart scroll stayed unchanged. Logs and the
standalone preview are in `build/benchmarks/20261122-rotated-timeline-return/`.

### Reveal function prototypes in short viewports

The Actions shortcut for a function's BTF prototype considers the visible
part of the detail pane as well as its nominal height. When space is limited,
it uses the chart area for reading, places the return arrow beside the
heading, and temporarily hides chart controls that are not needed there.
The declaration stays folded by default. Returning restores the split,
scroll position and Actions focus. On `ping3` slice 665, the first line,
Copy and Return were all reachable in eight touch viewport sizes, from
240 × 240 up to 844 × 390. Logs and preview are in
`build/benchmarks/20261122-btf-compact-prototype/`.

### Budget the first click separately

Use `--cold-frame-limit-ms MS` to fail a Timeline selection benchmark when
the first click exceeds an environment-specific two-frame budget. This is
separate from `--warm-frame-ratio-limit`, which skips the first batch. The
benchmark emits the measured `handler_ms` and `two_frames_ms` in a `cold_frame`
record. Two animation frames are a scheduling measurement, not proof of
screen presentation. Set the budget on the same report, browser, viewport
and CPU throttling used for the regression run.

In the prototype-reading `ping3` preview at 4× CPU throttling, 240 desktop
clicks and 120 touch taps on Windowed Text retained no extra DOM nodes or
event listeners between the first and final post-GC checkpoints. Early/late
warm p95 stayed at 31.6/31.5 ms on desktop and 32.8/32.8 ms on touch. A
separate 640-click desktop run with no forced collection during the series
measured 30.6/31.2 ms. Its only overlapping long task was the first click.
The cold desktop trace attributed 21.4 ms to one layout event; the tested
flex and containment variants did not yield a consistent improvement, so the
production layout was kept. Logs: `build/benchmarks/20261122-ping3-click-followup/`.

### Keep the selected call visible on short full-screen jumps

For touch viewports up to 360 × 300 CSS pixels, full screen after a Text
jump keeps Return, Tools, and Exit in the main row. Search and display
controls move into Tools; `/` opens Search there and Escape closes Tools
without clearing the query. Widening the viewport restores the controls
to the toolbar. The fallback notice for a row with no exact slice still
identifies the prior call rather than implying an exact match.

On `ping3`, at 240 × 240 the toolbar including the fallback notice dropped
from 228 px to 84 px. The prior call, notice and return control were
reachable in 14 touch checks across page and native full screen, and
returning restored the Text position. Logs and preview:
`build/benchmarks/20261122-unmatched-jump/`.

### Skip painting while a drag is held against the time limit

Mouse and single-finger chart drags and mouse drags in the overview keep a
fixed span for the gesture. They repaint only when the bounded time window
moves. Reversing far enough to move the window updates it on the next frame.
In `ping3`, six outward moves per edge and gesture went from six ruler and
six track paints to zero, with one of each on reversal. This counts avoided
draws, not input-to-display latency. Logs and preview:
`build/benchmarks/20261122-timeline-drag-limits/`.

Zoom buttons and shortcuts also leave the time window unchanged at the
corresponding zoom limit. A pinch still pans when its center moves the
window at a fixed scale. On `ping3`, three outward inputs per tested case
(buttons and shortcuts at both limits, pinch at the widest limit) went from
three ruler and three track paints to zero. The minimum-span pinch moved the
window by the same amount before and after the change. Logs and preview:
`build/benchmarks/20261122-pinch-zoom-limits/`.

### Leave a cleared Timeline alone on blank clicks

Clicking or tapping a blank chart area still dismisses the current slice,
range, or detail. Clicking that area when nothing is open or selected no
longer redraws the tracks and ruler. Drag, hover and double-click hit handling
are unchanged.

On the standalone `ping3` report at 1500 × 900, six blank clicks after
dismissing slice 665 went from six paints per canvas to zero. Track and ruler
pixels, time range and scroll positions matched. A separate 48-click run
alternating slices 665 and 2366 at 4× CPU throttling found no warm slowdown or
post-GC node or listener growth. It still recorded one overlapping long task
at the first selection. This change avoids blank-click work; it does not
remove the cold layout cost. Audit and preview:
`build/benchmarks/20261122-timeline-click-audit/`.

# Text tabs and Timeline round-trip interaction

November 13, 2026. This changes navigation and hover presentation, not trace
decoding, delivery thresholds or recorded data. Regenerate older standalone
HTML reports to receive the updated runtime.

## Problems and implementation

- Text's view tabs used to scroll above the viewport while its controls stayed
  pinned. A deep trace-line jump therefore left no visible Timeline tab.
  The shared Text/Timeline/Compare tab strip is now sticky, with an opaque
  theme-aware background. Text controls sit immediately below it.
- Tab and control height changes update their combined sticky inset only when
  the value actually changes. Anchor scrolling, task captions and experimental
  windowed Text retain clearance below both bars. Text-only reports without
  tabs keep their original control position.
- Switching views retains the independent Text reading position and the
  existing Timeline model, selection, time range and filter. Native/page full
  screen still hides and restores the same page chrome.
- Timeline used to replace its tooltip HTML on every mouse event, even over
  the same call. It also changed `left`/`top` before reading geometry on the
  next event, repeatedly forcing tooltip layout. Unchanged content now retains
  its DOM and movement uses a transform from a fixed origin. A bounded intrinsic
  width keeps the tooltip inside narrow windows. Changes of call, values or
  asynchronously loaded source information still refresh the content.
- A pending Text guide repaint now clears the guide canvas and returns before
  querying hidden Text geometry when another view is active. Text guides still
  redraw normally after returning to Text, scrolling, resizing or changing
  their settings.
- Hover hit testing, tooltip formatting and geometry reads now consume only
  the latest pointer position in the next scheduled canvas frame. The same
  callback applies the hover state and paints the highlight/cursor, rather than
  adding a second animation-frame delay. Intermediate pointer positions which
  cannot be painted no longer synchronously construct and measure tooltips.
  Leaving the canvas, scrolling, starting a drag/touch gesture or suspending the
  view cancels queued hover work. Source-request cancellation and complete
  tooltip contents are retained.

The earlier scroll-only headless baseline did **not** establish a repeatable
persistent slowdown after every Timeline → Text → Timeline round trip. The
subsequent source-expansion reproduction below identifies a separate native
browser cost which tooltip/event optimizations alone cannot fix.

## Source expansion and native selection

The important reproduction is **Timeline → Text → click a trace row to open
its source → Timeline**, not just scrolling Text. In a headed Chromium 145 run
of the 38,938-row real report, opening three neighboring source panels around
Text line 1,360 makes the problem repeatable. The browser leaves an empty
native text selection (a caret) on the clicked Timeline tab. Blink repeatedly
canonicalizes that position across the large, `content-visibility:hidden`
Text subtree during its frame lifecycle. This is native browser work, not slow
JavaScript tooltip formatting: the trace shows `VisibleUnits::canonicalPosition`
and `EditingUtility::nextCandidateAlgorithm` taking hundreds of milliseconds.

The view lifecycle now clears empty native caret ranges when entering or
reselecting a non-Text view. Real Text selections are temporarily suspended
before hiding Text, then restored on return with their ranges and direction;
clearing a selected passage permanently would lose useful reading state.
Editable controls retain their own caret/selection. Removed ranges are not
restored. Text DOM, expanded source panels, source links, keyboard-selected
rows, scroll position and the complete Timeline model remain in place.

In a matched, serial headed Chrome profile with 100 real pointer moves after
opening source, the measurements are:

| After opening source and returning | Previous runtime | Selection lifecycle fix |
| --- | ---: | ---: |
| Pointer-profile wall time | 31.91 s | 1.87 s |
| Main-thread task time | 31.98 s | 0.47 s |
| Maximum animation-frame interval | 350 ms | 16.8 ms |
| JavaScript time | 149 ms | 171 ms |
| Native caret canonicalizations | 100 | 0 |

The small script-time difference cannot explain the roughly 30-second delay,
distinguishing this fix from the earlier hover coalescing change.
These are local profile results, not universal timing
guarantees. An independent intervention clearing native selection on the old
runtime also removes the delay without detaching Text. A nonempty Text
selection exercises the same expensive browser path and is suspended as well.
The original `write4.html` stays unchanged; `write4.source-hover.html` is the
updated local preview. Both public samples receive the same runtime-only fix.

## Repeated entry into large inline Text

This is separate from the native-selection hover issue above. The existing
38,938-row, approximately 63 MiB standalone report retains about 597,000 DOM
elements. Entering Text does not parse its trace or rebuild its model again,
but restoring the hidden full Text tree invalidates expensive browser paint
work. In the serial headed Chrome profile, repeated entry takes roughly
1.04–1.16 seconds to a double-animation-frame feedback proxy even though script
execution accounts for only about 2 ms. The main Paint span alone takes about
710–780 ms. Nested Paint event durations must not be summed as independent work.

Previously, browser offscreen layout skipping applied only to the chunked
sidecar mounting path. Complete inline reports now opt in at **4,096 Text rows**,
including headers/comments, after the same capability checks. This also works
without Timeline. Small inline reports, unsupported browsers and print keep
their full native layout. The explicit windowed renderer is unchanged.

Simply enabling the sidecar styles is not sufficient: the real report's
unvisited long rows would shrink its horizontal extent from 2,435 to 2,099 CSS
pixels. Inline mode therefore batches real, trace-only intrinsic width
measurements for its existing 512-row paint blocks. Expanded source is excluded
from that temporary measurement, but remains fully laid out while open.
Filtering, folding, reordering and narrow/wide font changes refresh affected
widths and remembered block sizes; warm view switches perform no such
measurement. If Text is hidden, dirty blocks are queued until Text entry,
before restoring selection and reading position. This avoids imposing full
width measurement on a saved Timeline-first startup. No rows, raw arguments,
source panels or analysis records are removed. There is no new network resource,
worker, threshold for sidecar output, or promise of bounded DOM/model memory.

Two alternating baseline/fixed pairs of fresh, serial headed Chrome 145 runs
on that same report give the following local medians. Each browser makes three
entries at the top and three after an actual source-row click at Text line 1,360:

| Feedback proxy | Previous runtime | Inline block rendering |
| --- | ---: | ---: |
| First Text entry after Timeline startup | 3,751 ms | 2,136 ms |
| Warm entry at the top | 1,086 ms | 37.8 ms |
| Entry with source already expanded | 1,147 ms | 46.9 ms |

All four runs have identical complete row/metadata, source-panel and Timeline
SHA-256 digests in both phases, and identical reading positions. The complete
Timeline JSON digest is
`2c185313a114a2d02e576ffb13cf08e8b180003f19cd59359dc06b3fac714a80`.
The updated local preview is `write4.text-entry.html`; the prior previews and
original input are not overwritten. A separate 100-pointer-move profile after
opening three source panels still has a maximum rAF interval of 16.8 ms.

These are not universal speed guarantees. Saved-Timeline reload through ready
is 1.32–1.38 s in the baseline pair and 1.50–1.66 s in the fixed pair, so this is
not a claim that every startup metric improves. Complete eager DOM preparation
and additional layout bookkeeping still exist. Width measurement itself stays
deferred until Text is shown, and the first Text entry is not a warm-switch
result. Never present the warm figures as cold loading or a DOM-memory bound.

Reproduce feedback and full Text/source/Timeline digests outside timing windows:

```sh
xvfb-run -a python3 tests/benchmark_textentry.py /path/to/report.html \
  --rounds 3 --source-line 1360 --headed --executable /usr/bin/google-chrome \
  --output /tmp/text-entry.json
```

Choose an expandable original Text line for `--source-line`, or omit it to
measure top-of-report entry only. Run comparisons serially, in fresh browsers,
without tests, builds or screenshot generation in parallel. Double-rAF feedback
is a proxy, not proof of actual frame presentation; initial measurement and
later geometry refreshes still require full native layout and can be expensive.

## Regression coverage

```sh
PYTHONPATH=.:tests python3 -m unittest \
  test_text_layout test_textchunks test_timeline_hover test_textviewport \
  test_textwindow test_detail_actions test_detail_expand \
  test_timeline_fullscreen test_timeline_resize test_detail_layout \
  test_detail_colors test_mixed_sample test_mixed_tree_guides test_report \
  test_timeline_names test_timeline_sources test_cpu_coverage test_sidecar \
  test_browser -v
```

The source-expansion update passes **546 tests** with Playwright/Chromium
available, including all six hover tests, Text round trips, tooltip bounds and
resizing. The wheel test waits for
the requested scroll delta before taking its snapshot; a pre-existing nonzero
scroll position alone does not mean the next compositor scroll has finished.
The compact-toolbar
navigation test starts each theme on a fresh page so an unfinished smooth scroll
from the preceding theme cannot interfere with the next setup's instant jumps.

The viewport suite covers single/sidecar delivery, light/dark themes, mobile and
desktop widths, dynamic tab height, deep anchor navigation, and exact vertical
and horizontal reading-position restoration. Repeated real trace-line clicks
and visible-tab returns check complete Timeline JSON, selection, range, Text
row order and Text content. Same-target pointer motion must retain the tooltip
nodes and avoid per-event layout or hidden Text measurements. Tooltip target
changes, viewport bounds, source loading/cancellation, dense slices, high-DPI
scene rendering and full-screen focus are also covered by adjacent suites.

These changes do not unload already prepared Text, remove source/argument
information, force sidecar mode or replace the complete analysis model with a
viewport subset. Default Text still initializes the full DOM; that initial
work, inline width measurement and memory cost can remain substantial on large
traces.

The inline block-rendering suite checks the 4,095/4,096-row boundary, capability
fallbacks and Text-only output. Full-layout comparisons cover long unvisited
arguments, source open/close, filtering, sorting, task/time order, nested folds,
responsive fonts, print and hidden-Text resizing. Cross-block native mouse
selection, browser find, complete mixed user/kernel records, flags, call
boundaries and source links remain available. Repeated warm view switches keep
the same row/source nodes, complete Timeline JSON, selection and reading
position, with no repeated width measurement.

For this inline-rendering update, full unittest discovery runs **1,387 tests**
with Chromium, raw decoding and a fresh build of the pinned uftrace plus its
current patch series: 1,384 pass and three optional tests skip because no
`FG_VMLINUX`/`FG_KERNEL_SRC` or `FG_TRACES` are configured. A separate Firefox
159 check preserves both native selection ranges and their exact endpoints
after source expansion and repeated view changes.

The source-expansion regressions add real row clicks, repeated visible-tab
returns and same-tab reselection across both delivery modes, themes and DPR
1/2. They check the native selection invariant rather than relying on flaky
wall-time limits, and compare complete Timeline JSON, hover pixels, source DOM
identity/content, row order, keyboard selection and reading position. Forward
and backward selected passages must restore their exact endpoints and text;
search editing is not interrupted and removed selections are discarded.
The two new source/selection tests fail in 12 subcases with the previous boot
asset, then pass with the lifecycle fix.
An additional Firefox check restores two native selection ranges with identical
text and endpoints. Both sample screenshots, the Text screenshot and the detail
guide screenshot regenerate byte-identically. JavaScript syntax and 387 local
documentation paths also validate successfully.

### Latest-pointer regression

An 80-event burst alternating a slice and empty space previously performed
**40 tooltip replacements and 120 canvas/tooltip bounds reads synchronously**
before the browser could paint. It now performs none in the mouse handlers,
then **one replacement and two bounds reads** for the final slice in the
scheduled paint callback. The final canvas is pixel-identical to hovering that
slice alone, with identical tooltip text. These are deterministic work counts,
not a claim that the user's persistent delay has been reproduced locally.

`test_timeline_hover` covers both delivery modes, both themes and DPR 1/2;
real Text scrolling and repeated returns preserve the complete Timeline JSON,
selection, time range, Text row order/content and reading position. Queued
events cannot revive tooltips after leaving, scrolling, dragging, touching or
switching views. Adjacent source tests retain stationary asynchronous tooltip
updates and cancellation.

## Real-report measurements

The standalone real report eagerly contains and initializes Text even when
Timeline is selected at startup. The measurements in this section therefore
compare **already prepared Text** before/after a round trip, not before/after
the first deferred Text initialization. The new hover regression separately
starts sidecar reports with the report-prefixed saved Timeline preference,
asserts that Text has zero mounted rows, then opens Text, uses real wheel input
and returns repeatedly. Writing an unprefixed `localStorage.view` does not set
FuncGraph's per-report view preference.

The local kernel-only report contains **29,586 calls and 38,938 Text rows**.
Each pointer profile uses three serial before/after pairs in independent
Chromium instances, alternating their order, at 1500 × 900. No tests, screenshot jobs or
builds run concurrently. Each hover sample has 36 real pointer moves over four
neighboring pixels, with an animation frame between moves; CDP metrics exclude
page loading and Text initialization. The same sample is measured before and
after a real trace-line click and return through the visible Timeline tab.

For unchanged-content motion inside the widest recorded call,
`__x64_sys_read` (call index 20,809), the medians are:

| Stage | Baseline layouts | Updated layouts | Baseline script time | Updated script time |
| --- | ---: | ---: | ---: | ---: |
| Before Text round trip | 71 | 0 | 10.8 ms | 7.5 ms |
| After returning from Text | 71 | 0 | 10.8 ms | 7.0 ms |

The updated tooltip retains its original DOM node in all three runs, before
and after the round trip; the baseline replaces it in every run.

For dense-neighbor motion, where tooltip content changes, the medians are:

| Stage | Baseline layouts | Updated layouts | Baseline script time | Updated script time |
| --- | ---: | ---: | ---: | ---: |
| Before Text round trip | 71 | 35 | 100.8 ms | 93.2 ms |
| After returning from Text | 71 | 35 | 91.0 ms | 90.1 ms |

These are totals across the 36 moves, not per-frame latency or a claim of a
large end-to-end speedup. Dense-pixel explanations and changing call values
still replace their tooltip content; they are not incorrectly reused to
improve the counts. Pointer motion over empty/header space produces zero
layouts both before and after this change.

### Follow-up burst measurements

The current `write4.html` snapshot has SHA-256
`5057fed8e2c0998fbcc9076deb3657695fe3154f9a0c32a2b4b4ebc7840c2f72`.
Three independent Chromium 145 baseline/updated pairs run serially, alternating
their order. Each profile dispatches 16 synthetic pointer events per frame for
60 frames (960 events), with the final position alternating between a call and
another target. Target DOM nodes are cached outside the timed event loop.
The same profile runs before and after real Text wheel scrolling to 64,000 px.
After returning to Timeline, the medians are:

| Work across the profile | Previous runtime | Latest-pointer runtime |
| --- | ---: | ---: |
| Tooltip replacements | 450 | 30 |
| Canvas/tooltip bounds reads | 1,440 | 90 |
| Layouts | 901 | 61 |
| Layout time | 40.0 ms | 6.8 ms |
| Total script time | 152.1 ms | 142.3 ms |

The same work-count reduction holds before Text scrolling. Final tooltip text
and canvas pixels match the corresponding single-target hover in every run;
complete Timeline JSON stays identical across all six browser instances.
This is a synthetic input-pressure test, not the user's mouse frequency or an
end-to-end latency speedup: formatting now occurs in the paint callback, so
dispatch-handler time alone would overstate the gain. A separate 100-real-move
profile keeps a maximum frame interval of 16.8 ms both before/after scrolling,
with either runtime. That scroll-only experiment did not reproduce the
source-expansion delay; the later source-specific reproduction and fix are
documented separately above.

Both samples and `write4.hover.html` receive only byte-reversible runtime asset
replacements; the original `write4.html` is untouched. Sample/guide screenshots
remain byte-identical after regeneration. The new filename also avoids
confusing a previously loaded standalone browser tab with the updated runtime.

The old tab strip is about 608,978 pixels above the viewport after the deep
trace-line jump; the updated strip stays at `top = 0` in every pair. The complete
Timeline JSON, selection and time range remain identical in all runs. Desktop
and narrow dark-theme screenshots also verify that the tabs and Text controls
remain visible together at a scroll position of 12,000 pixels.

Both embedded sample reports receive runtime-only, byte-reversible asset
updates: restoring the previous assets recovers each previous HTML exactly,
including all trace and source payloads. Sample and guide screenshots are
regenerated and remain byte-identical at their existing initial view positions.
JavaScript syntax, 184 local documentation links/anchors and `git diff --check`
also pass. The original real report is not overwritten; the updated local
preview at that stage uses `write4.fixed.html`; the current source-expansion
preview is `write4.source-hover.html`.

## Timeline source controls on repeated selection

The Timeline detail pane handles source-ready, disclosure and source-layout
events at the pane boundary instead of registering new handlers for each
selected call. Before replacing or closing a panel, it saves any user change
to the disclosure that the browser's queued `toggle` event has not handled
yet. Initial markup and replaced panels no longer write the preference;
closing and immediately selecting another call no longer reopens the source
or starts an unnecessary fetch. Loading, errors and retry are unchanged.

In a Chromium run with 35 distinct clicks on the same `ping3` slices, the
number of listener registrations on the disclosure and source container fell
from 70 to 0 during selection. Writes of the unchanged source-open preference
fell from 35 to 0. Both versions displayed a source excerpt for the same 32
selections. This counts avoided work, not a measured frame-time improvement or
evidence of a persistent memory leak.

Replacing or closing a Timeline detail also unregisters that panel's source
excerpt and layout control immediately. The source-mode switch no longer walks
entries from old panels while waiting for garbage collection. Text rows and
panels retained during a tab switch keep their existing source-mode behavior.

On offline `ping3`, a regression probe clicked 20 different slices and held
references to each panel's source nodes to make collection timing irrelevant.
The source-mode index held up to 40 Timeline entries before the change and at
most two after it; closing Details left zero instead of 40. All 20 excerpts
matched. This tests index membership, not browser memory usage or click
latency. The log is in `build/benchmarks/20261119-trace-jump/source-index-teardown-ab.jsonl`.

## Reuse an unchanged Timeline canvas on return

Returning to Timeline still measures the split and resumes pending work. It
only redraws the tracks if their geometry, scroll position, pixel ratio, or
paint state changed. Suspending a hovered chart marks the old highlight dirty
so it is removed on return. Theme changes while Text is open also still
repaint both canvases.

On standalone `ping3` with Windowed Text mounted, 12 tab round trips at each
of 1500 × 900 and 844 × 390 dropped from two track paints per return to zero.
Both canvases were pixel-identical to the initial view after every return,
with the same selected slice. In these Chromium runs at 4× CPU throttling,
median browser script time for a return was 9.71 → 2.11 ms on desktop and
3.26 → 2.12 ms in touch emulation. These are script times, not display
latency. Logs: `build/benchmarks/timeline-resume-paint/`.

The toolbar observer also retains its last visible measurements while Text
is active. Returning with the same toolbar size no longer schedules another
split calculation. Visible toolbar growth and changes made while hidden
still reach the chart. Twelve round trips per viewport on the same offline
`ping3` data at 1500 × 900 and 844 × 390 each reduced Timeline root size
reads from two to one per return, with identical selection, canvas dimensions,
and canvas pixels. Chromium's layout count did not change; the probe measures
avoided work, not a latency improvement. Logs:
`build/benchmarks/timeline-control-observer/`.

## Keep the Text reading position after rotation

When Windowed Text is hidden, the report remembers the line immediately under
the toolbar and how far that line is scrolled under it. If a width change
invalidates row heights, it restores that reading position after the existing
window rebuild. With unchanged width and projection, returning reuses the
existing window. Return restores focus to the jump's source row only if it is
still visible and focus has not moved elsewhere during the rebuild.

On the same standalone `ping3` data in Chromium touch emulation, both
844 × 390 → 390 × 844 and the reverse kept `L63548` under the toolbar within
0.5 CSS pixels of the starting offset and restored focus to `L63550`. Before
the change, the first rotation moved the reading line to `L63547`; the reverse
kept the line but changed its offset by 12.5 CSS pixels. The window still held
160 rows. Log: `build/benchmarks/return-rotated-text/ab.jsonl`.

## Reuse an in-flight Sidecar source chunk across selections

Reselecting the same call keeps its existing source panel and request. When a
different recorded call needs another excerpt from the same external chunk,
the old panel still cancels immediately. The existing download can be reused
while the new source disclosure is waiting for its first visibility callback.
If no subscriber takes over, the provider schedules cancellation after 120 ms.
Other chunks and standalone reports retain the existing cancellation behavior.

In the Chromium Sidecar test, holding the first source response and switching
between two excerpts in the same chunk previously aborted the fetch and
requested the same URL again. The new path makes one HTTP request and renders
only the replacement excerpt. A separate provider test covers expiration and
immediate cancellation when the new excerpt belongs to a different chunk.

## Recheck the first recorded-call selection

With Windowed Text already mounted and Chromium 145 at 4× CPU throttling,
the first 1500 × 900 `ping3` canvas click spent 19.6 ms in the measured event
handler and 20.4 ms in its longest subsequent layout event. The same CSS/font
probes initially suggested a font fallback effect, but four runs per variant
with rotated order did not show a consistent improvement from changing the
monospace stack. The existing typography stays in place. The separate
argument-splitting improvement reduces formatter work without establishing a
first-paint improvement. Logs: `build/benchmarks/ping3-first-selection/recheck.Z6Ej4y/summary.jsonl`
and `build/benchmarks/ping3-first-selection/fonts.V5N3OX/results.jsonl`.

## Repeat the original ping3 slice-click workload

At 1500 × 900 in Chromium 145 with 4× CPU throttling, alternate canvas clicks
between slices 665 and 2366. Over 40 clicks, the original `ping3.html` showed
roughly **232 ms** median mouse-up handler time in both the first and last
ten clicks. The current build stays around **3–4 ms** when opening Timeline
directly, after Windowed Text, and after Full Text (82,529 mounted rows).
This reproduces severe per-click latency in the original report, but not
progressive per-click degradation in this workload. Two-frame callback
medians were about **250 ms** in the original and **24–29 ms** in the current
build; callbacks are not a measurement of screen presentation.

The new audit also reports browser long tasks overlapping the selection
sample windows, not startup or isolated forced GC. In a 40-click run on the
shared Windowed Text preview, one overlapping task lasted 54 ms, and node and
listener counts were unchanged at all post-GC checkpoints. A separate
six-click check on the old report recorded seven overlapping long tasks
(143–359 ms). Logs: `build/benchmarks/selection-current-audit/run.xAythQ/`.
Reproduce the Windowed Text check with the standalone preview:

```sh
python3 tests/benchmark_timeline_selection.py /vol_1t/Qemu/x86_64/share/ping3-current-preview-20261121.html \
  --text windowed --indices 665,2366 --batches 20 --gc-every 5 \
  --retained-growth-limit 128,0 --warm-frame-ratio-limit 1.20
```

The warm latency check compares the p95 two-frame callback time of the last
three batches with the first three warm batches, excluding the cold first
batch. The ratio is a repeatability check, not an absolute frame-time budget.

On the `ping3` compact Text toolbar preview, Chromium 145 at 4× throttling
passed the check with Windowed Text on desktop (240 clicks, 30.82 → 31.50 ms)
and landscape touch (120 taps, 32.45 → 32.52 ms), and after mounting all
82,529 rows in Full Text (180 clicks, 31.32 → 31.00 ms). Identical post-GC
checkpoints showed no increase in retained nodes or listeners in any of these
three runs. A separate 480-click desktop run without forced GC during sampling
measured 31.31 → 31.40 ms. Cold detail opening is outside this drift check.
Logs: `build/benchmarks/20261121-click-stability/`.

### Coalesce pending call-detail work

Navigation, the child summary and the time breakdown can defer work until
after the first paint. The detail pane now shares one frame callback and
schedules only jobs for the current selection. Work keeps its existing time
budgets, and leaving Timeline still pauses it until return.

On `ping3`, a scripted burst of 60 selections in one browser task went from
180 detail-specific animation-frame callbacks and 180 timers to one callback
and three timers. This measures bounded scheduling work, not pointer latency.
Separately, 240 real canvas clicks across 20 slices in the pre-change preview
showed no progressive slowdown: the first and last 60 handler medians were
3.45 and 3.30 ms at 4× CPU throttling, and post-GC node and listener counts
were stable. Logs: `build/benchmarks/20261121-selection-varied/`.

## Save Text state before updating the return button

On November 21, 2026, the warm standalone `ping3` trace showed that showing
or hiding the return button before leaving Text invalidated the layout.
Reading the native selection then laid out the still-visible Text view before
the switch laid out Timeline. The switch now saves Text selection and scroll
state, including the Windowed Text reading anchor, before updating the button.
It still clears an empty caret, but does so only once when leaving Text.

With Windowed Text already mounted in Chromium at 4× CPU throttling, the
median layout count fell from **3 to 2** when jumping from Text to Timeline
and from **2 to 1** when using **Return to Timeline**, at both 1500 × 900 and
844 × 390. Returning to Text and plain tab switches were unchanged. These
counts cover each action and its settling frames. The follow-up traces confirm
the pre-switch layout is gone; the change did not establish a consistent
two-frame latency improvement. Logs: `build/benchmarks/view-roundtrip-warm/`.

Full Text behaved differently in the 1500 × 900 baseline: switching tabs spent
about 140 ms recalculating styles at the same throttling rate. The Windowed
Text result does not address or measure a fix for that larger cost.

## Keep style containment stable when leaving Full Text

Large Full Text now keeps `contain: style` on its content root in every view.
The existing hidden Text rule still uses `content-visibility: hidden`; explicit
style containment avoids changing that boundary when hiding the document. In
isolation, `contain: layout` and `contain: paint` alone did not remove the cost.
There is no new layout or paint containment on visible Text, and the change
does not alter Windowed Text, small reports, or Text-only reports.

Three warm tab clicks per direction on standalone `ping3` (82,529 mounted
rows, Chromium at 4× CPU throttling) gave these medians when **leaving** Full
Text for Timeline:

| Viewport | Style/layout-tree time, before → after | Click to second animation frame, before → after |
| --- | ---: | ---: |
| 1500 × 900, mouse | 146.4 → 2.2 ms | 159.7 → 26.6 ms |
| 844 × 390, touch | 130.0 → 2.1 ms | 140.0 → 25.3 ms |

The second animation frame is a proxy, not measured screen presentation.
Coming back to Full Text still spent 129–140 ms in style/layout-tree work in
the new trace; skipping the horizontal scroll write only moved work out of
the handler, so that experiment was not kept. Traces and isolation logs:
`build/benchmarks/full-text-warm/`.

## Keep inactive Timeline and Compare out of the document flow

Removing Text's own visibility changes did not remove the remaining delay when
returning to Full Text. In the same `ping3` report, disabling only the optional
view's display toggle removed it. Timeline and Compare now keep their flex
containers while inactive. Their roots have no size in the document flow,
and hidden content is neither painted nor focusable. Browsers without
`content-visibility: hidden` support retain the display toggle. The views still
suspend their own work, and startup, loading cancellation, and data delivery
are unchanged.

An inactive Timeline root is exempt while it still matches `:fullscreen`.
Switching back from Text can happen before the browser finishes exiting
native full screen. Collapsing the root with `content-visibility: hidden`
during that interval reproduced a stall in Chromium. The ordinary inactive
rule applies again as soon as the browser exits full screen; no delay is
added to view switching.

Three warm returns from Timeline to Full Text at each viewport, using the same
82,529-row standalone report and 4× CPU throttling, gave these medians:

| Viewport | Style/layout-tree time, before → after | Click to second animation frame, before → after |
| --- | ---: | ---: |
| 1500 × 900, mouse | 140.1 → 1.0 ms | 174.7 → 41.4 ms |
| 844 × 390, touch | 128.7 → 1.2 ms | 160.7 → 41.7 ms |

The reverse direction stayed at 24–26 ms to the second animation frame. This
is not actual display latency, and does not cover cold Full Text initialization.
Isolation logs and traces: `build/benchmarks/full-text-resume/`.

## Skip focus geometry for non-focusable Text rows

**Return to Text** restores the reading position even when a plain trace row
cannot receive focus. In that case it now focuses the Text tab directly,
without measuring or hit-testing the row. Focusable rows still get the
visibility check, so a hidden or covered row cannot take focus.

Four returns to line 1021 of `ping3` in both 844 × 390 Windowed Text and
1500 × 900 Full Text dropped from one focus-related row measurement per
return to zero. Scroll positions and focus targets matched. These are
avoided reads, not a measured end-to-end latency improvement. Logs:
`build/benchmarks/20261122-roundtrip-latest/real-return.log`.

## Reuse Text guide rows between scroll frames

The guide canvas still measures the viewport and repaints during scrolling,
but reuses the visible row lists for groups that remain in view. A Text
revision change discards those lists, including after folds, filters, order
changes, window slides and renderer switches. Groups that scroll out of view
are dropped from the row cache. Disabling guides drops the whole cache.

On November 22, 2026, eight warm scroll steps within one 160-row window of
the standalone `ping3` report reduced group row queries from **8 to 0** (1,280
candidate row visits avoided per run). This held in Chromium at 1500 × 900 and
844 × 390 in both time and task order. The smaller viewport also panned
horizontally. The old and new reports matched in guide-canvas pixels before
and after scrolling, row and scroll positions, mounted rows, and the sticky
task label. These are work counts, not measured latency. Audit script, logs,
and report: `build/benchmarks/20261122-text-guide-rows/`.

## Reuse bounds when drawing Text guides

When the mounted Windowed Text rows span the guide viewport, their bounds
are enough to decide whether the canvas can paint without also measuring the
full Text root. At the edges and in Full Text, the root still defines the clip.
Both renderers reuse the bounds of the first intersecting row from the search
that located it. Bounds are read again on the next paint instead of caching
positions across frames.

Eight in-window `ping3` scroll steps at both 1500 × 900 and 844 × 390, in time
and task order, saved **24 bounding-rectangle reads** per run: eight each for
the Text root, the mounted row group, and the first intersecting row. The
guide pixels, reading position, and mounted rows matched, including at the
start and at the end after the destination rows had loaded. The counts do not
measure frame latency. Logs and the standalone preview are in
`build/benchmarks/20261122-text-guide-geometry/`.

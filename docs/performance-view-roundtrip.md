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

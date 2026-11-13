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

The local headless baseline did **not** establish a repeatable persistent
slowdown after every Timeline → Text → Timeline round trip. It did establish
the lost tabs and redundant per-event tooltip work. These fixes remove that
confirmed work and test repeated round trips; they are not evidence that every
possible source of large-report jank has been eliminated.

## Regression coverage

```sh
PYTHONPATH=.:tests python3 -m unittest \
  test_timeline_hover test_textviewport test_textwindow test_detail_actions test_detail_expand \
  test_timeline_fullscreen test_timeline_resize test_detail_layout \
  test_detail_colors test_mixed_sample test_mixed_tree_guides test_report \
  test_timeline_names test_timeline_sources test_cpu_coverage test_sidecar \
  test_browser -v
```

The **540 existing tests** pass with Playwright/Chromium available. The three
new hover tests also pass in a final **10-test focused run** covering hover,
Text round trips, tooltip bounds and resizing. The new wheel test waits for
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
work and its memory cost can remain substantial on large traces.

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
with either runtime. The locally unconfirmed persistent delay remains a
limitation, rather than being reported as conclusively resolved.

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
preview uses `write4.fixed.html`.

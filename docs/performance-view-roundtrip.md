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

The local headless baseline did **not** establish a repeatable persistent
slowdown after every Timeline → Text → Timeline round trip. It did establish
the lost tabs and redundant per-event tooltip work. These fixes remove that
confirmed work and test repeated round trips; they are not evidence that every
possible source of large-report jank has been eliminated.

## Regression coverage

```sh
PYTHONPATH=.:tests python3 -m unittest \
  test_textviewport test_textwindow test_detail_actions test_detail_expand \
  test_timeline_fullscreen test_timeline_resize test_detail_layout \
  test_detail_colors test_mixed_sample test_mixed_tree_guides test_report \
  test_timeline_names test_timeline_sources test_cpu_coverage test_sidecar \
  test_browser -v
```

All **540 tests pass** with Playwright/Chromium available. The compact-toolbar
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

## Real-report measurements

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
| Before opening Text | 71 | 0 | 10.8 ms | 7.5 ms |
| After returning from Text | 71 | 0 | 10.8 ms | 7.0 ms |

The updated tooltip retains its original DOM node in all three runs, before
and after the round trip; the baseline replaces it in every run.

For dense-neighbor motion, where tooltip content changes, the medians are:

| Stage | Baseline layouts | Updated layouts | Baseline script time | Updated script time |
| --- | ---: | ---: | ---: | ---: |
| Before opening Text | 71 | 35 | 100.8 ms | 93.2 ms |
| After returning from Text | 71 | 35 | 91.0 ms | 90.1 ms |

These are totals across the 36 moves, not per-frame latency or a claim of a
large end-to-end speedup. Dense-pixel explanations and changing call values
still replace their tooltip content; they are not incorrectly reused to
improve the counts. Pointer motion over empty/header space produces zero
layouts both before and after this change.

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

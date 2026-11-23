# Timeline hover previews

Hover is a preview; selecting a slice opens its full details. New reports keep
the preview inside the visible viewport, including short landscape windows and
mobile visual-viewport offsets. If the preview cannot fit, its body is clipped
and a separate **Select for full details** footer makes that limit explicit.
Long unbroken text wraps instead of silently overflowing horizontally. This
does not remove recorded arguments, symbols, source positions or flags. The
existing 180-character argument preview is unchanged; selected details retain
the complete value.

## Layout work

The tooltip previously set its display style and measured its bounding box on
every pointer move, even when only its position changed. It now reuses its size
while the rendered content and viewport dimensions are unchanged. Movement
still updates the preview position and existing slice hit-testing/highlights.

Content changes, hide/reentry, viewport resize/scroll, asynchronous source
completion, observed size changes and completed font loading invalidate the
cached size or dismiss the preview. Source errors use the same bounded renderer
and retain escaped error text. A late source response cannot revive a dismissed
preview. Browsers without ResizeObserver still refresh on content changes,
hide/reentry, viewport changes and font completion; arbitrary CSS mutations
that change size without those signals are not detected in that fallback.

Scrolling the page, the visual viewport, or the Timeline pane clears the time
cursor and slice highlight together with the preview. A viewport resize also
discards the old hover, but a mouse move already waiting for the next paint
still takes effect if its coordinates hit the canvas after layout. This extra
hit check is limited to resize frames; ordinary moves keep the existing
one-paint coalescing and do not do an additional DOM hit test.

Call-site positions shared by hover and selected-call details keep the 256
most recently used results. This includes positions recovered from legacy
source excerpts and their cached no-position results. Source loading and
validation still use the separate report-data cache; eviction only recomputes
position text, inline status and its link when needed again.

Probing all 58,539 `ping3` calls in Chromium, then revisiting them in reverse,
retained 2,921 → 256 position entries with identical result digests. This
measures only this cache, not total browser memory or click latency. Raw
results: `build/benchmarks/20261120-novel-slices/cache-parity.jsonl`.

## Dense call hit testing

Pointer moves and clicks reuse the last thin-call group in the same CSS
pixel and row while the view and filter are unchanged. Exact hits on wide
calls still take precedence. The filtered result belongs to the current
match cache; changing Search or the Text filter recomputes the match count
and representative. Neither path keeps a history of visited pixels.

At 4× CPU throttling in Chromium 145, replaying 30,000 filtered hits in the
same 20-call `ping3` pixel took **43.4 → 37.0 ms** (median). Replaying 5,000
filtered hits in a synthetic pixel with 399,999 calls took **35.0 → 6.4 ms**.
Both warm loops skipped all repeat group lookups. The neighboring-pixel hits
and settled tooltip text matched, and the real alternating-pixel loop took
**43.0 → 42.7 ms**. These are isolated hit-test timings, not frame or click
latency. Logs: `build/benchmarks/dense-call-hit/ab-changing.jsonl`.

## Reproduce the comparison

Keep an HTML report generated before the change, regenerate it with the new
runtime, then run with Playwright and its Chromium browser installed:

```bash
mkdir -p build
python3 tests/benchmark_tooltip.py /tmp/before.html sample.html \
  --screenshots build/tooltip-comparison > build/tooltip-results.json
```

The benchmark injects a **synthetic** call with long function/caller names and
arguments into each report before Timeline initialization. It does not alter
the saved report. Each viewport gets a fresh browser context. Fonts and three
animation frames settle before measuring 12 same-slice moves. The benchmark
also selects the call and verifies the full symbol/arguments and unchanged
model. The measurements exclude initial tooltip creation and invalidations.

Local Chromium results on November 17, 2026:

| Viewport | Before: geometry reads / fits | After: geometry reads / fits |
|---|---|---|
| 1500 × 900 | 12 / yes | 0 / yes |
| 390 × 400 | 12 / no | 0 / yes |
| 844 × 390 | 12 / no | 0 / yes |
| 320 × 480 | 12 / no | 0 / yes |

These count tooltip `getBoundingClientRect()` calls, not every browser layout
operation. They do **not** establish overall FPS, physical presentation latency,
startup speed or elimination of all hover delays. Content changes still need
layout; large reports still retain their other parsing, rendering and memory
costs. Regenerate older reports to embed the fix.

## Regression coverage

`tests/test_tooltip.py` checks viewport bounds and the conditional footer,
complete selected details and immutable data, repeated hover after expanding
Text source, content and font-size changes, native/fallback fullscreen,
visual-viewport offsets, missing ResizeObserver, asynchronous source success,
escaped failure and cancellation. Existing browser regressions additionally
cover cursor alignment, high-DPI pixels, compact/legacy source positions, inline
frames, dense groups, wakeups and decoded flags.

## Cursor overlay

The cursor line and time badge are now a separate, non-interactive overlay.
The badge fits beside the line at the bottom of the tracks instead of sharing
the ruler with span, range and selection text. Moves over the same hover target
leave both base canvases unchanged; moving to a different target can redraw
the track canvas to update the highlight without repainting the ruler.
Scrolling, starting a drag, touch,
leaving the tracks or switching views clears stale hover state.

Hover changes repaint the tracks only if they change what is drawn. A
recorded slice below the dense threshold has no hover outline, and an
already-selected recorded slice keeps its selection outline. Moving between
such targets still updates the tooltip and time badge. Synthetic frames,
waits, CPU segments, KVM events and changes to wakeup focus keep their
existing repaint behavior.

On `ping3`, an identical 30-position sweep across a busy row with wakeups
enabled reduced track paints from 30 to 9 at both DPR 1 and 1.25. All 60
track images and their tooltip texts matched. This counts paints, not
end-to-end latency. Logs: `build/benchmarks/20261120-dense-hover-paint/`.

The tooltip reuses the formatted body for the current slice until the hover
target, overhead mode, or asynchronously loaded source position changes.
Pointer placement and dense-pixel warnings still use the current hit. Leaving
the slice clears the cached body. If a source request fails, the warning stays
visible while the pointer moves within the same slice, without rebuilding or
remeasuring the tooltip. If another action loads that source, the next move
replaces the warning with the resolved position when available.

In the 58,539-call `ping3` report, 30 settled moves within the same call after
its first hover reduced body formatting from 30 to 0 at both DPR 1 and 1.25.
All 60 tooltip HTML bodies and positions matched. This counts formatting
work, not latency. Logs: `build/benchmarks/20261120-hover-slice-content/`.

Moving within the same dense off-CPU pixel now reuses the last group lookup
for that track, depth and viewport. An interval containing the exact pointer
time still takes precedence; a gap in a dense pixel still previews and opens
its first grouped wait. Moving to a different pixel or view geometry computes
the new group instead.

For 30 settled moves in synthetic captures with 16,000 and 200,000 waits,
the median reads from the track's wait array fell from 53 to 15 and from
316 to 19 per move, respectively. Tooltip text and the clicked wait matched
before and after, including exact hits after zoom. These are record-read
counts, not frame times; they include occasional unrelated redraw reads.
The real `ping3` sample has 129 waits and does not enter this grouping path.
Logs: `build/benchmarks/20261120-dense-wait-hover/wait-hit-ab.jsonl`.

The time label paints into a DPR-scaled canvas bounded by 112 × 20 CSS pixels,
not a changing text node or another plot-sized backing store. The cursor layer
does not participate in track layout, and its writes precede tooltip geometry
reads in the same frame. This preserves the existing two-layout bound for
first and repeated hover in the Text/Timeline round-trip regression, in both
themes and delivery modes.

On Chromium 145.0.7632.6, two reports generated from `samples/sample_io.txt`
were compared with 24 synthetic moves over the same empty track area, waiting
two animation frames per move. At 1500 × 900 and DPR 2, base-scene canvas copies
went from 48 to 0 (about 99.9 million source pixels submitted by the old path),
and two full-size scratch canvases became unnecessary. The same copy count
held at DPR 1 and at 390 × 740 with DPR 1.25. These are API work counts, not
measurements of actual memory traffic, frame rate or user-visible latency.

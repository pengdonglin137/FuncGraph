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
cover scene reuse, high-DPI pixels, compact/legacy source positions, inline
frames, dense groups, wakeups and decoded flags.

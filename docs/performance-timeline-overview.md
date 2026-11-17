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

# Horizontal Text reading without moving report controls

November 12, 2026. This fixes Text layout and navigation, not trace decoding or
windowing. Regenerate existing reports; single-file and sidecar use the same
body wrapper and keep every original Text row, argument and source record.

## Problems and implementation

- Long trace lines expanded the document's horizontal extent. On a real mixed
  sample at 390 CSS px, the old document was 1,548 px wide. The existing `h/l`
  handler scrolled `.container`, which had no horizontal scroll range, so it
  did not move the trace. This is not evidence that an ordinary touch tap
  necessarily moves the document horizontally.
- `#textViewport` surrounds only `#content` and owns horizontal overflow.
  Header, tabs and toolbar keep the viewport width. It has no fixed height;
  window still owns vertical scrolling. Printing uses visible overflow.
- `h/l` and uppercase variants pan that body by 100 CSS px, retaining smooth
  scrolling and input-field editing. Legacy markup without the wrapper falls
  back to the document scroller. Touch and mouse/trackpad scrolling use native
  browser scrolling, without a gesture interception layer.
- Native `scrollIntoView` with the new nested horizontal scroller could put
  early keyboard targets behind the sticky toolbar. Text keyboard and anchor
  navigation now scroll window using the row geometry and toolbar boundary,
  retaining the horizontal position. Existing far-jump instant/nearby smooth
  behavior and stale-anchor protection remain in place.
- View changes save horizontal and vertical Text reading positions separately.
  The hidden wrapper keeps a layout box at zero height instead of setting an
  ancestor to `display:none`, which would prevent invalidation of remembered
  offscreen block sizes during a responsive layout change.
- Chunked Text remembers already laid-out inline sizes with
  `contain-intrinsic-inline-size: auto 0px`. Otherwise skipping an unusually
  wide block during a far vertical jump could shrink the horizontal range and
  reset the reading position. Longer unvisited rows can still extend it;
  this is not an exact initial maximum-width calculation. Existing block-size
  invalidation for content and responsive changes remains active.
- The existing viewport-sized hierarchy canvas repaints on local body scroll
  and clips to the body bounds below the toolbar. It does not query every row
  or introduce a second full-trace rendering path.
- A 320 px non-touch window also exposed an additional source of overflow in the
  toolbar: a non-wrapping heatmap legend and a 200 px filter minimum. Legend
  colors/thresholds now wrap as complete pairs and the input may shrink while
  retaining its value. Apply/Clear/help stay in the window, without changing
  desktop/touch control modes or hiding trace records.

Filtering away wide content and widening the viewport may legitimately clamp
the scroll position. There is no per-view vertical scroll container, forced
line wrapping, argument truncation, changed call hierarchy or new dependency.
Complete DOM/model memory is still unbounded, and final Text layout can block.

## Large-trace comparison

Input: `/tmp/pi4/traceB.txt`, 72,176 records / 55,186 calls, without vmlinux or
source bodies. Baseline: `144eeb2d9f96`; both sides use
`--no-config --no-btf --timeline --enable-fold --filter --report-mode sidecar`.
Three alternating pairs use independent Chromium 145.0.7632.6 processes at
1500 × 900 through real `fgview` HTTP. No other test/build workload runs during
these measurements. Medians are observations, not performance guarantees.

| Observation | Before | After |
| --- | ---: | ---: |
| First Timeline ready, ms | 186.7 | 191.4 |
| Complete Text ready, ms | 3258.5 | 3270.9 |
| Parsed-status feedback, ms | 71.7 | 78.0 |
| Longest Text long task, ms | 192 | 191 |
| Sampled Text renderer RSS, MiB | 797.6 | 801.2 |
| Mounted Text rows / model records | 72,176 / 72,176 | 72,176 / 72,176 |
| Paint blocks / skipped blocks at first Text | 141 / 140 | 141 / 140 |
| Text network resources | 18 | 18 |

All six complete Text model digests agree:
`bfcd594748ec635ac430b942433cff198a922f635af4ddf97573afe30fc1f9c6`.
Browser errors/CSP violations are zero. These results show no material opening
regression in this fixture, not a universal speedup or memory reduction.
Two-rAF/status feedback is a paint-opportunity proxy; sampled RSS is neither a
peak nor an upper bound. The source-free trace cannot establish performance
for source-rich reports; browser fixtures separately exercise source behavior.

## Reproduce and verify

```bash
python3 funcgraph.py /path/to/traceB.txt --no-config --no-btf \
    --timeline --enable-fold --filter --report-mode sidecar --output REPORT_DIR
PYTHONPATH=tests python3 tests/benchmark_text_delivery.py BEFORE_DIR AFTER_DIR \
    --rounds 3 --output viewport-benchmark.json
PYTHONPATH=tests python3 -m unittest -v test_textviewport test_textchunks
```

The viewport tests cover single/sidecar, light/dark, 320/390/1500 px, long complete
arguments, source expansion, filtering/task ordering, h/l and uppercase,
editing, actual CDP touch swipes, mouse wheel, vertical anchors/keyboard,
view roundtrips, narrow non-touch toolbar keys/actions, legacy markup,
hierarchy repaint and actual transparent pixels outside the text region.
The old runtime fails the new isolation
regression. Existing chunk tests additionally cover responsive hidden-view
size invalidation, print, far jumps, stale anchors, cancellation/retry,
selection across blocks and real mixed-capture metadata.

## Verification results

- Full unittest discovery: 1,137 tests in 751.374 seconds, successful with
  three external-data skips (`FG_VMLINUX`, kernel `scripts/faddr2line`
  comparison and `FG_TRACES`). Raw, staged native uftrace and sample-browser
  requirements were enabled. The runner emitted an unclosed-socket
  `ResourceWarning` after reporting success; this layout change does not
  alter the viewer server lifecycle.
- Seven real-kernel golden configurations passed with the original
  `tests/golden.py` normalization. Replacing only the changed runtime assets
  and adding the wrapper reproduces each new report; normalization was not
  broadened to ignore trace or source differences.
- Both regenerated samples retain byte-identical Text bodies and identical
  source/Timeline/comparison data, apart from the existing volatile build-time
  statistic. Each sidecar's 20 resources passes byte-count/SHA verification;
  all 18 Text chunks and the filter index match the baseline.
- Wheel and sdist builds pass isolated-install smoke tests: 14 tests each,
  with one native-wheel-only skip for these pure-Python builds. All 17 packaged
  frontend assets match the checkout byte for byte. This does not claim a new
  native uftrace build or release.
- JavaScript syntax and whitespace checks pass. At 320 px without touch
  emulation, the document and client width both measure 305 px (the scrollbar
  consumes the remainder); long Text remains independently scrollable.

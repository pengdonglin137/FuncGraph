# CPU hover interrupt summaries

The November 18, 2026 optimization removes repeated IRQ-history scans while
moving over the same CPU segment. It does not add a setting, change the preview
layout or remove evidence. Existing HTML needs regeneration to embed the fix.

## Root cause and bounded reuse

Every settled CPU pointer move called `tipCpu()`, which scanned that CPU's whole
`CPU_IRQ` history. Even when the preview's text was unchanged and its layout size
was already cached, softirq, hardirq and NMI overlap totals were recalculated.
The history scan therefore remained proportional to the capture size on each
move, rather than to the visible segment.

The formatter now retains only the last interrupt summary, keyed by the CPU and
both segment boundaries. Another point in the same segment reuses those totals.
Changing CPU, start or end recomputes them. Returning to an older window after
visiting another one recomputes it too: this is a single-entry cache, not an
ever-growing map of hovered spans. Empty histories and zero-overlap windows
retain their zero totals as well.

The cache contains only numeric keys and four numeric totals. It neither copies
the history nor builds an index at startup. `CPU_IRQ` is constructed once from
the recorded call geometry for each Timeline instance; source completion,
filters, track visibility, theme changes and overhead estimates do not rewrite
those measured intervals. The task name and other preview fields still format
normally instead of caching the entire HTML under an IRQ-only key. Pointer
position, hit-testing, selection and highlight painting are unchanged.

The initial visit to a new window still scans that CPU's complete history.
Repeated HTML formatting, rendering and any tooltip layout invalidation still
take time. This change does not claim to solve initial report loading, the first
hover over a huge history, every move across different segments, or general FPS.

## Exact meaning and fidelity

The original strict overlap tests and arithmetic are retained:

```text
interval.end > segment.start && interval.start < segment.end
contribution = min(interval.end, segment.end) - max(interval.start, segment.start)
```

Records are accumulated in their existing order and separately by softirq,
hardirq and NMI context. Touching endpoints contribute nothing. Nested contexts
can overlap; the three values are not mutually exclusive CPU-utilization
buckets. A zero total remains omitted from the preview, just as before.

The CPU label still distinguishes spans backed by `sched_switch` records from
CPU-tagged observation spans. Without usable switches, such a span does not
prove uninterrupted execution or identify task/main lifetime boundaries.
Neither these labels nor recorded function times, flags, sources, arguments,
returns, CPU runs or selection are changed by the optimization.

`tests/test_cpu_hover.py` checks exact independently clipped totals, nested
contexts, equal endpoint boundaries, different CPUs and changed start/end
boundaries. It also checks that task labels do not become stale, and that an
older window is not retained in an unbounded cache. Actual pointer movements
cover kernel and mixed captures in single-file and sidecar mode, light/dark
themes and device pixel ratios one and two. Text source expansion and a return
to Timeline retain the model and summary. A 25,000-interrupt fixture verifies
one initial scan followed by zero history lookups across 32 repeated previews;
the baseline fails that assertion with 32 lookups.

## Reproduce the measurements

`tests/benchmark_cpu_hover.py` instruments temporary report copies. It counts
history lookups and the number of records they expose only during CPU tooltip
formatting, excluding unrelated rendering reads. It also times that formatter;
those durations are not end-to-end pointer or physical presentation latency.
Each side must retain the complete `tlData` input except generated build time,
the same segment and byte-identical full tooltip HTML. Otherwise the
comparison fails rather than timing different content.

With Playwright and Chromium installed, compare the saved sample with baseline
`0f3ee23cf918`:

```bash
git show 0f3ee23cf918:sample.html > /tmp/cpu-hover-before.html
python3 tests/benchmark_cpu_hover.py /tmp/cpu-hover-before.html sample.html \
  --output build/cpu-hover-sample.json
python3 tests/benchmark_cpu_hover.py /tmp/cpu-hover-before.html sample.html \
  --interrupts 100000 --moves 24 --rounds 3 \
  --output build/cpu-hover-stress.json
PYTHONPATH=tests python3 -m unittest \
  test_cpu_hover test_cpu_coverage test_timeline_hover test_tooltip -v
```

The stress option replaces the input in temporary copies with one synthetic
task span and 100,000 interrupt roots, not a real capture. Its first preview
still visits 100,000 records on both sides. Across 24 repeated previews, history
lookups drop from 24 to zero and visited records from 2,400,000 to zero. The
benchmark alternates execution order across three fresh-context pairs and
retains all formatter timings rather than claiming a universal speedup.

Local Chromium 145.0.7632.6 results at 1500 × 900, median totals over the 24
direct repeated formatting calls after the initial preview:

| Input | IRQ roots on selected CPU | Records visited: before → after | Formatter time: before → after |
| --- | ---: | ---: | ---: |
| Public kernel sample | 4 | 96 → 0 | 0.1 ms → 0.1 ms |
| Captured `write4.log`, 29,586 calls | 16 | 384 → 0 | 0.2 ms → 0.1 ms |
| Synthetic interrupt history | 100,000 | 2,400,000 → 0 | 24.6 ms → 0.1 ms |

The real-capture comparison uses a Timeline-only report without source or BTF
resolution; the original large source-rich report is untouched and is not the
timed artifact. The small-case timings are near the browser timer's resolution
and establish no perceptible speedup. The stress case deliberately has many
interrupt roots; it is not representative of every large trace. Only repeated
scans are eliminated: each first preview still visits its complete history.

The checked-in kernel and mixed samples are regenerated with their matching
kernel image, pinned source revision and user source tree. Their five JSON
payloads remain identical except generated build time. A complete HTML
comparison after substituting only the new runtime and normalizing generation
metadata also preserves all Text content, source panels and attributes.
The final relevant regression run passes 195 tests, followed by 38 sample,
documentation and navigation tests, all without skips. Five regenerated
sample/guide PNGs match their previous bytes exactly. Optional-browser test
discovery also skips cleanly when Playwright is not installed.
Validation artifacts are retained locally under `build/cpu-hover-audit/`, not
included in the package. Related work: [bounded preview layout](performance-tooltips.md)
and [overview density reuse](performance-timeline-overview.md).

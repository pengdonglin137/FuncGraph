# CPU interrupt interval queries

The November 18, 2026 follow-up removes whole-history scans from **new CPU
preview windows and CPU-track repaints** when the requested range is narrow.
It adds no controls, changes no colors and discards no recorded interrupts.
Regenerate existing HTML to embed the updated viewer.

## Why the previous cache was not enough

The [single-entry CPU summary cache](performance-cpu-hover.md) avoids repeated
work inside one segment. Moving to a different CPU or segment still scanned
that CPU's entire interrupt history. Every CPU-track repaint also inspected all
its interrupt roots, even when zoomed into a tiny part of the trace.

CPU interrupt records are start-sorted, but their **ends need not be sorted**:
a softirq can contain shorter hardirq or NMI intervals. Binary-searching those
ends without checking their ordering could silently omit long overlapping
intervals. The viewer must also retain their original paint order.

## Shared index, unchanged evidence

CPU queries reuse the scheduling interval index. Its optional time-column
argument accommodates CPU tuples `[start, end, context]`; existing scheduling
tuples and callers retain their default columns.

- Fewer than 128 records: keep the original linear traversal, with no index entry.
- Monotonic starts **and** ends: binary-search the first possible intersection,
  then visit in original order until starts pass the right edge. The index is
  an ordering marker, not a retained array of per-record offsets.
- Otherwise: keep each block of 128 records' minimum start and maximum end in
  a `Float64Array`. Skip blocks outside the requested range, then check records
  individually in their original order. Arbitrary overlap and unordered inputs
  remain valid for the shared visitor.
- Each indexed CPU history yields at its 16,384-record boundaries through the
  existing Timeline loading mechanism. It does not defer correctness until
  after the first paint.

The original CPU arrays, call columns and scheduling evidence are not rewritten.
The index is built once per Timeline instance, not rebuilt on pointer moves,
filter changes, theme changes or Text/Timeline round trips. The most recent
numeric summary still avoids any history lookup for repeated previews.

Rendering keeps inclusive endpoint intersections and the same colors, widths
and layering. Preview arithmetic retains its stricter endpoint checks and
accumulation order: touching intervals add zero. Nested context totals remain
separate, potentially overlapping sums, **not CPU-utilization percentages**.

This is not a constant-time interval tree. Nonmonotonic queries still inspect
block bounds; a long interval in every block can force a full scan. A full-range
paint must still draw every relevant interrupt, with some visitor/index overhead.
No sampling, merging, density substitution or hidden-event cap is introduced.

## Measurements

`tests/benchmark_cpu_intervals.py` compares complete input data, complete preview
HTML and CPU-strip PNG bytes before reporting results. It alternates before/after
order over fresh browser contexts and waits for view-switch layout to settle.
Its synthetic fixtures partition CPU runs into 128,000 ns segments; the nested
variant includes softirq roots containing shorter hardirq and NMI intervals.
These are controlled fixtures, not claims about the distribution of real traces.

Record-access counting uses temporary array proxies, including binary-search
reads and block candidates. **Separate timing passes disable those per-record
proxies** and verify the same content and pixels again. Times measure direct
formatter calls or CPU-track drawing submissions, not physical presentation,
total pointer latency or whole Timeline frame rate.

Chromium 145.0.7632.6, 1500 × 900, three alternating pairs, medians for 24 CPU
window queries/paints over 100,000 interrupt roots:

| Fixture | Record accesses, before → after | Preview time | CPU-track paint time |
| --- | ---: | ---: | ---: |
| Monotonic ends | 2,400,000 → 1,962 | 12.3 → 0.1 ms | 13.7 → 0.5 ms |
| Nested, nonmonotonic ends | 2,400,000 → 4,480 | 12.0 → 0.1 ms | 12.9 → 0.6 ms |

The nested index retains 12,512 bytes of numeric block bounds. The monotonic
case retains only its per-CPU ordering marker, although construction temporarily
allocates block bounds. Active index-building time was 0.6–0.9 ms and 0.9–1.2 ms
respectively, with six loading yields each; these timings exclude time between
generator steps. Timeline-ready timing varied between runs and establishes no
startup speedup.

A full-range query still visits all 100,000 roots (plus 17 binary-search reads
in the monotonic case). Full-range paints measured 26.2 → 27.4 ms and
26.5 → 27.0 ms; this optimization does **not** claim a full-view rendering gain.
The public kernel sample has only four interrupt roots and uses the linear path;
its submillisecond results do not establish a perceptible improvement.

```bash
git show 2b4d89f6ca97:sample.html > /tmp/cpu-interval-before.html
python3 tests/benchmark_cpu_intervals.py /tmp/cpu-interval-before.html sample.html \
  --interrupts 100000 --rounds 3 --output build/cpu-interval-ordered.json
python3 tests/benchmark_cpu_intervals.py /tmp/cpu-interval-before.html sample.html \
  --interrupts 100000 --nested --rounds 3 --output build/cpu-interval-nested.json
PYTHONPATH=tests python3 -m unittest test_cpu_hover test_cpu_coverage \
  test_timeline_hover test_tooltip -v
```

## Regression evidence

The CPU browser tests check both tuple layouts, empty/small/threshold-sized
histories, monotonic and unordered intervals, exact boundaries, zero-length
queries, early exit and original visitation order. A 25,000-root fixture checks
independently clipped totals, new-window access counts, full-range completeness,
single-entry reuse, bounded index storage and cooperative loading yields.
Substituting the previous runtime reproduces 25,000 record accesses where the
new narrow-window assertions require fewer than 512.

Pixel comparisons against linear traversal cover nested contexts and touching
endpoints, light/dark themes, DPR 1/2 and indexed single-file/sidecar reports.
Existing actual-pointer tests retain kernel/mixed inputs, both delivery modes
and Text source-expansion round trips. The regenerated public samples retain
all five JSON payloads except generated build time; full HTML equality after
runtime substitution and generation-metadata normalization preserves Text rows,
source panels and attributes as well.

Validation completes 503 distinct regression tests: the 428-test main run's
optional `FG_TRACES` case is explicitly rerun with the preserved `write4`,
host-QEMU and QEMU kernel captures, and 75 additional sample, documentation and
detail tests pass. A final nine-test run includes that real-capture case and all
eight CPU browser tests. Five regenerated sample/guide PNGs match their previous
bytes exactly. Optional-browser discovery separately skips all eight CPU tests
cleanly when Playwright is unavailable.

Local measurements and validation logs are retained in
`build/cpu-interval-audit/`, not shipped as report data.

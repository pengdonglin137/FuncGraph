# Reusing measured and corrected self times

## Change and invariants

Previously every Overhead switch ran three full passes over the recorded
calls: copy durations and find their maximum, subtract each recorded child
from its parent, then clamp negative self times and find their maximum. This
repeated immutable calculations on every switch, including a request for the
already active basis or a request without an available correction.

The measured basis now owns its self-time column and both color-scale maxima
and logarithms. The corrected basis is computed lazily on first use with the
same three passes, subtraction order, floating-point arithmetic and zero clamp.
Returning to either basis reuses its existing column and scale values. No-op
requests do not scan the parent column. Saved-view restoration uses this same
path; duration-array identity still changes with the active basis so existing
analysis caches keep their invalidation semantics.

The measured self-time column is also the read-only source for time
breakdowns, rather than a separate copy. Before correction is used, there is
one recorded-call-sized self column instead of two: 8 × N fewer column bytes.
After first use there are two self columns, as before, not a third permanent
cache. This is a storage-accounting statement, not a peak browser-memory
measurement; duration columns, inputs, synthetic frames and other indexes
remain allocated independently.

No trace data, call count, parent, duration, source location or frame is
discarded. Synthetic inline and untraced frames are not additional recorded
children. Measured start/end geometry and breakdowns remain measured in both
bases. Self/total color scales, details, tooltips, range summaries, analysis
panels and CSV exports still follow the chosen duration basis. No controls or
new settings are added.

Only the cached self-metric selection becomes constant-cost. First correction
still computes all corrected self times; panel refresh, analysis and drawing
can still perform input-sized work. In particular, deferring the second column
allocation can make first correction slower. This does not promise faster
first loading or universally shorter display latency.

## Reproduction and evidence

```bash
python3 tests/benchmark_overhead.py /tmp/before.html /tmp/after.html \
  --scenario plain --pairs 300000 --rounds 3 --output /tmp/overhead-plain.json
python3 tests/benchmark_overhead.py /tmp/before.html /tmp/after.html \
  --scenario inline --pairs 120000 --rounds 3 --output /tmp/overhead-inline.json
python3 tests/benchmark_overhead.py /tmp/before.html /tmp/after.html \
  --scenario plain --pairs 300000 --rounds 3 --detail --output /tmp/overhead-detail.json
python3 tests/benchmark_overhead.py /tmp/before.html /tmp/after.html \
  --scenario original --rounds 3 --detail --output /tmp/overhead-sample.json
```

The benchmark clicks the production Overhead control programmatically, with
and without selected-call details. It records model-switch time, complete
synchronous button-handler time and a two-rAF readiness proxy separately.
It alternates report order in fresh browser contexts and measures the first
activation without precomputing corrected metrics. Each run performs six
switches; full SHA-256 verification runs outside the timed intervals.

Each basis must have identical complete measured/adjusted duration and self
columns, maxima and logarithms across versions and repeated switches. Complete
time/depth/parent/task/name/flag/line/inline-depth columns, measured self,
strings, synthetic metadata and track rows must stay unchanged by correction.
Inputs must match except for generated build time. Counts alone or matching
sampled rows are insufficient. Synthetic pairs use measured durations
`[9000, 4000]`, correction `[6000, 2000]`, one recorded child, 1000 ns per
descendant and 2000 ns per call; inline context is not charged as extra calls.
They are stress fixtures, not claims about a captured workload.

### Measured switches

Chromium 145.0.7632.6, 1500 × 900, three alternating fresh-context pairs;
baseline `df077d321456` versus cached metrics. Median synchronous complete
button-handler milliseconds, before → after:

| Input | Calls / total frames | First corrected activation | First return to measured | Repeated switches 3–6 |
| --- | --- | --- | --- | --- |
| Synthetic plain pairs | 600,000 / 600,000 | 4.6 → 5.4 | 4.7 → 0.3 | 3.9 → 0.2 |
| Synthetic inline pairs | 240,000 / 600,000 | 2.5 → 2.9 | 12.3 → 0.3 | 1.35 → 0.3 |
| Plain pairs, root detail open | 600,000 / 600,000 | 6.4 → 6.9 | 6.4 → 1.9 | 5.2 → 1.8 |
| Captured kernel sample, detail open | 485 / 773 | 4.0 → 4.1 | 3.6 → 3.7 | 3.6 → 3.6 |

For plain pairs, median repeated model-switch work fell from 3.6 ms to below
the timer's 0.1 ms resolution; with details open it fell from 3.3 ms to below
that resolution. Zero in the raw logs is not a promise of zero execution
time. First activation became 0.4–0.8 ms slower in these synthetic cases,
consistent with deferring the corrected column allocation. The small captured
sample showed no meaningful handler improvement. Two-rAF medians stayed near
33 ms in all cases: less synchronous work did not demonstrate a faster next
display frame in this setup. There were no concurrent test browsers during
measurement, and all complete state fingerprints matched.

Node regressions independently check ordered subtraction, clamping, fractional
and large values, overlapping intervals, parents stored after children,
empty/all-zero reports, missing estimates, no-op requests, stable column
identity, lazy parent reads, synthetic parent extensions and frozen inputs.
Browser coverage checks repeated detail and heat-color refreshes, unchanged
measured breakdowns/geometry, sidecar delivery, saved views and existing
duration-basis exports and analysis behavior.

The final audit passes 255 distinct tests with no skips, including three real
kernel captures, native-required raw decoding, uftrace import, parser, report,
source and bilingual usage/release-document checks. Both public HTML samples
are regenerated using kernel source revision
`7475ea8b58616b33c04afb976cf0fbaad9dd63fb` and unchanged GitHub `v7.4-rc2`
links. All five JSON payloads per sample match after excluding only Timeline
build time. The full HTML matches after replacing the model module and
generation metadata. Five regenerated public screenshots are byte-identical.

Raw measurements, baseline-failure proof and validation logs are retained in
`build/overhead-switch-audit/` in the development checkout.

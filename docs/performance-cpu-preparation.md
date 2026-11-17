# Cooperative CPU track preparation

The November 18, 2026 follow-up extends
[cooperative Timeline preparation](performance-timeline-preparation.md) to CPU
runs and interrupt roots. The aim is shorter uninterrupted work and cancellation
inside these stages, **not a promise that the complete report opens faster**.
Existing reports need regeneration to embed the change.

## Previously unbatched work

After yielding `Preparing scheduling context…`, the viewer synchronously
collected every task's CPU runs, sorted/clipped them, created three typed
columns, scanned every recorded call for interrupt roots, and sorted those
roots. A single long CPU history could therefore hold the main thread until
that whole operation finished, even though inline/frame preparation was already
cooperative. A 300,000-root fixture measured a 19–23 ms CPU step during the
initial investigation; a deliberately unordered case exceeded 65 ms.

## Preserve the complete model

The CPU builder is now a generator using the existing 8,192-operation batch:

- Collect runs across tracks, including skipped/invalid candidates, with
  progress and cancellation points.
- Sort by start time using the existing bounded chunk-sort/merge machinery,
  extended to ordinary tuple arrays as well as typed identity rows. Equal-start
  CPU records retain their original order, exactly like the previous stable
  native sort; task-row callers retain their original comparator and labels.
- Clip each preceding run's end in the same forward order as before. Do not
  merge runs, invent scheduling boundaries or change equal-start task ownership.
- Fill the same `Float64Array` start/end and `Int32Array` task columns in batches.
- Scan all recorded CPU/IRQ candidates, retaining the same parent-context
  suppression, missing-CPU handling, CPU insertion order and interrupt-only CPUs.
- Sort interrupt roots stably, including nonmonotonic/nested intervals, before
  constructing the existing [range index](performance-cpu-intervals.md).

Already sorted histories are validated in batches without copying/reordering
them. Unordered long arrays use native chunks of at most 8,192 records and a
cooperative stable merge. The merge retains one history-sized reference buffer
plus a bounded chunk copy; typed task rows retain their existing typed buffer.
This is not a bound on total memory or a peak-memory improvement.

All partially built Maps, columns and sort buffers remain private to the loading
generator. Cancel/Back to Text/Escape closes it; a later attempt rebuilds from
the unchanged cached input, never from a half-clipped or half-sorted history.
Existing loading thresholds and view/input policies remain unchanged. Small
embedded reports still synchronously drain the same generator; this adds no
setting, button, changed colors, network requests or new tracing requirement.

## Measurements and limits

`tests/benchmark_preparation.py` now fingerprints complete CPU lists, run columns
and ordered IRQ tuples as well as the existing complete call/frame/track model.
Every comparison must match those hashes and all input data except generated
build time. Verification runs outside the measured preparation window.

Chromium 145.0.7632.6, 1500 × 900, three alternating fresh-context pairs; median
milliseconds, baseline `5d3671a21112` → updated viewer:

| Controlled input | Longest CPU preparation step | Longest overall generator step | Preparation elapsed | Two-rAF readiness proxy |
| --- | ---: | ---: | ---: | ---: |
| 300,000 nested roots, already sorted starts | 20.5 → 4.9 | 20.5 → 15.7 | 164.9 → 172.1 | 378.0 → 380.7 |
| 300,000 roots, permuted starts and reversed CPU runs | 69.8 → 5.5 | 69.8 → 29.3 | 292.4 → 363.3 | 571.8 → 649.6 |

CPU preparation step maxima across individual updated runs were 2.7–4.9 ms and
2.9–7.6 ms respectively. The CPU metric includes collection, sorting, clipping
and filling, not all other model stages. An 8 ms scheduler budget remains soft:
JSON parsing, allocation, unbatched work, final full-range paint and garbage
collection can still block. Overall preparation-window Long Tasks were not
eliminated: median longest tasks were 98 → 101 ms and 139 → 145 ms, including
work outside the newly batched CPU stages.

The unordered case also incurs more active CPU preparation work: stable chunk
merging and yielding cost more than one native sort. Its individual updated
readiness proxies ranged from 640.8 to 790.7 ms, versus 559.8–576.9 ms before.
These results demonstrate shorter uninterrupted CPU stages, **not universal
speedup, a hard responsiveness deadline, faster physical presentation or lower
memory use**. The synthetic distributions are not claims about every capture.

To reproduce, generate both sides with identical inputs/options, or extract
the pinned baseline sample before using the current generated sample:

```bash
git show 5d3671a21112:sample.html > /tmp/cpu-preparation-before.html
python3 tests/benchmark_preparation.py /tmp/cpu-preparation-before.html sample.html \
  --scenario cpu --pairs 300000 --rounds 3 \
  --output build/cpu-preparation-unordered.json
python3 tests/benchmark_preparation.py /tmp/real-before.html /tmp/real-after.html \
  --scenario original --rounds 3 --output build/cpu-preparation-real.json
PYTHONPATH=tests python3 -m unittest test_timeline_preparation test_cpu_hover \
  test_cpu_coverage test_timeline_hover test_tooltip -v
```

The `cpu` fixture keeps nested softirq/hardirq/NMI parent geometry but permutes
whole groups, reverses CPU run input, and uses `--pairs` as the IRQ-root count
(one additional outer call). It is distinct from the benchmark's older
`gaps`/`inline`/`interleaved` parent-child pair fixtures. The ordered measurements
use the nested fixture from `tests/benchmark_cpu_intervals.py` unchanged.

## Correctness and cancellation

Node tests compare complete CPU output against an independent native-sort and
clipping reference. They cover no calls, non-timed modes, invalid runs, empty
tracks, non-IRQ/missing-CPU records, ordinary/idle tracks, nested same-context
suppression, interrupt-only CPUs, multiple CPUs, equal-start ownership, stable
ties and partial chunks around 8,192/16,384 records. Already sorted tuple arrays
must not enter the copying path. Frozen original input remains reusable after
canceling each CPU stage.

Browser tests gate the real loading generator during run collection, sorting,
clipping, typed-column filling, IRQ collection and IRQ sorting. They cancel via
the existing button, verify no partial canvas or changed cached input, release
the stale continuation, and reopen with all 25,000 roots and 8,335 CPU segments.
Escape is checked separately during IRQ collection. Substituting the previous
runtime reproduces missing cooperative progress in both run and IRQ scans.
The synthetic fixture also tests partial groups and counts sharing a factor
with the initial stride, so the chosen permutation cannot duplicate/omit times.

Validation completes 525 distinct passing tests: 449 main regressions (including
the preserved `write4`, host-QEMU and QEMU captures), 75 sample/document/detail
tests and the final 22-case preparation run, with overlaps counted once. All
these runs pass without skips. Optional-browser discovery separately runs the
18 fixture/Node tests and cleanly skips four browser tests without Playwright;
the benchmark entry point also verifies a small complete-model comparison and
creation of a new output directory.

The public kernel/mixed samples are regenerated with the pinned kernel/source
revision. Full payload/HTML and screenshot comparisons check that the change is
preparation-only, not a reduction in displayed evidence. Local measurement and
test artifacts are retained under `build/cpu-preparation-audit/`. All five
regenerated sample/guide PNGs match their previous bytes exactly; both reports'
five JSON payloads match except generated build time, and their complete HTML
matches after runtime substitution and generation-metadata normalization.

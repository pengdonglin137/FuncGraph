# Cooperative Timeline model preparation

Newly generated reports batch the model work behind the existing cancellable Timeline loading status. They keep the same complete model, drawing and input policy; they do not make reports use sidecar automatically.

The later [CPU preparation follow-up](performance-cpu-preparation.md) also batches
run/IRQ collection, stable sorting, clipping and typed-column filling. The
measurements below describe the earlier frame/task-row iteration, not that
follow-up's readiness or memory trade-offs.

## What changes

- Inline and untraced-frame scans count both recorded calls and expanded levels toward an 8,192-operation batch. One deeply nested call can yield without waiting for thousands of other calls.
- Synthetic column copies and row filling yield between batches. Inline frames remain before untraced frames, with the same names, identities, extents, context masks, parents and complete child lists.
- Track scans, synthetic grouping, depth grouping and row population yield as well. Already ordered rows are checked cooperatively and not rewritten. Unordered rows sort native chunks of at most 8,192 identities, then merge cooperatively with the original comparator: start time, then exact original identity.
- Models stay private until complete. Cancel/Back to Text/Escape closes the generator; reopening rebuilds from unchanged cached input. Incomplete columns or rows never become a ready canvas. Existing uftrace user/kernel/mixed inputs do not acquire invented untraced frames.

The existing staged-loading policy is unchanged: external Timeline data and embedded Timeline JSON of at least 1 MiB use cooperative scheduling; the embedded threshold measures JSON characters, not trace lines or complete HTML bytes. Small embedded reports retain synchronous preparation. Regenerate older reports to get the new implementation.

## Limits and trade-offs

This is main-thread cooperation, not a Worker, viewport virtualization or bounded model memory. The scheduler's 8 ms budget is soft: a single JSON parse, allocation, native chunk sort, other unbatched stage, layout or garbage collection can exceed it. Merge sorting an unordered long row needs an additional row-sized `Int32Array`; full inputs, columns, child membership and track indexes remain retained. Smaller execution batches can increase total preparation time because the browser schedules more tasks. Responsiveness, computation time and complete readiness must be reported separately.

No tracing, dependency pins, bundled native programs, release version, public captures or public samples change.

## Reproduce

Generate both sides with the same input and CLI options. The baseline for this iteration is `c28eda80240e`:

```bash
python3 funcgraph.py /path/to/trace.txt --no-config --no-btf \
  --timeline --no-text-view --output /tmp/after.html
python3 tests/benchmark_preparation.py /tmp/before.html /tmp/after.html \
  --scenario original --rounds 3 --output /tmp/preparation-real.json
python3 tests/benchmark_preparation.py /tmp/before.html /tmp/after.html \
  --scenario gaps --pairs 120000 --rounds 3 --output /tmp/preparation-gaps.json
python3 tests/benchmark_preparation.py /tmp/before.html /tmp/after.html \
  --scenario inline --pairs 120000 --rounds 3 --output /tmp/preparation-inline.json
python3 tests/benchmark_preparation.py /tmp/before.html /tmp/after.html \
  --scenario interleaved --pairs 120000 --rounds 3 --output /tmp/preparation-interleaved.json
```

The benchmark instruments the production generator in temporary copies of single-file reports. It alternates report order, creates a fresh context per run, records each `next()` duration and preparation-window Long Tasks, and observes a two-rAF readiness proxy. It does not request the synchronous model API while preparation is pending: that API would drain the generator and invalidate a cooperative-scheduling comparison. Model hashing runs only after timing and Long Task collection.

`original` keeps the supplied real input. Synthetic cases replace only the Timeline payload: `gaps` creates three missing levels per parent/child pair; `inline` creates three inline frames per pair; `interleaved` alternates inline and directly recorded children at the same depth to exercise unordered row merging. They are stress fixtures, not evidence that any captured trace has those characteristics.

Input payloads must match after excluding only the generator's `stats.build_time`; source report byte sizes and SHA-256 identities are recorded too. Every compared run must have identical call/frame/track counts and SHA-256 digests for the complete time/depth/parent/task/name/flag/line/inline-depth/duration/self-time columns, strings, full synthetic metadata and track row orders. A missing or reordered member fails the comparison; the script does not sample the model or accept matching counts alone. Timing excludes this verification and is not a peak-memory measurement.

## Measured responsiveness

Initial cooperative-preparation rollout: Chromium 145.0.7632.6, three
alternating fresh-context runs per side, median milliseconds (before → after):

| Input | Calls / frames / tracks | Longest generator step | Preparation elapsed | Two-rAF readiness | Longest observed preparation task |
| --- | --- | --- | --- | --- | --- |
| Captured Pi4 trace | 55,186 / 55,213 / 12 | 13.0 → 5.6 | 54.9 → 49.4 | 92.9 → 93.9 | 0 → 0 |
| Synthetic missing depths | 240,000 / 600,000 / 1 | 85.5 → 11.7 | 225.5 → 268.9 | 271.2 → 307.0 | 88 → 0 |
| Synthetic inline frames | 240,000 / 600,000 / 1 | 107.0 → 11.9 | 285.7 → 294.0 | 324.8 → 333.2 | 108 → 0 |
| Synthetic interleaved rows | 240,000 / 420,000 / 1 | 83.4 → 12.3 | 217.5 → 242.1 | 261.3 → 278.0 | 85 → 0 |

All full-model digests match. The captured trace has 72,176 input records and
was generated without vmlinux/DWARF; it is not source-rich performance evidence.
The stress fixtures demonstrate shorter uninterrupted work, not universally
faster loading: additional scheduling increases total readiness time. Zero means
no Long Task was observed in the measured preparation window, not a guarantee
that no browser work exceeds a frame budget.

At that initial rollout, focused preparation coverage passed all 13 tests.
The isolated Timeline, browser, provider and report suites ran 324 tests with
one fixture-dependent skip. Real Pi4 and mixed fixtures passed 24
layout/theme/storage configurations and 96 scope comparisons. The combined
native-required worktree audit ran 964 tests: 960 passed, three skipped, and
one pre-existing pending Text-layout test failed because Chromium reported
`containIntrinsicBlockSize` with an `auto` prefix. That independent Text change
was not part of the initial preparation iteration.

## Track identity ranges: November 18 follow-up

After CPU preparation was made cooperative, task-row grouping still built an
ordinary array containing every recorded slice ID in a track, then concatenated
that array with synthetic IDs for each view. Those temporary history-sized
arrays duplicated a range already described by the track's `lo`/`hi` bounds.
The concatenations and synthetic `filter()` ran synchronously between yields.

The row builder now traverses the recorded `[lo, hi)` range directly, followed
by the existing synthetic IDs. It still performs the same maximum-depth,
per-depth count and row-fill passes, and retains every final typed row. Normal
rows include recorded calls and untraced frames; inline rows include those plus
inline frames. Synthetic selection also yields in 8,192-operation batches,
including candidates not selected for the normal view. The comparator remains
start time followed by original identity, including negative deltas, equal
starts, nonzero track offsets and empty depths. No input or synthetic list is
sorted or mutated during grouping.

This removes temporary recorded-ID lists and their joined copies, not the full
model, rows, synthetic metadata or sort buffers. It adds no controls and does
not change loading thresholds, cancellation/retry, source navigation, duration
correction, filters, drawing or the small-report synchronous path.

Chromium 145.0.7632.6, 1500 × 900, three alternating fresh-context pairs;
median milliseconds, baseline `194c9ef59048` → range-based grouping:

| Controlled input | Active task-indexing work | Longest task-indexing step | Preparation elapsed | Two-rAF readiness |
| --- | ---: | ---: | ---: | ---: |
| 300,001 calls, unordered CPU fixture | 90.4 → 68.1 | 20.3 → 15.3 | 358.9 → 302.7 | 636.0 → 572.9 |
| 240,000 calls / 600,000 frames, missing depths | 70.9 → 52.8 | 15.7 → 9.9 | 303.9 → 287.2 | 356.8 → 329.2 |
| 240,000 calls / 600,000 frames, inline | 83.4 → 71.8 | 13.1 → 20.0 | 317.2 → 302.9 | 365.3 → 352.6 |
| 240,000 calls / 420,000 frames, interleaved rows | 84.2 → 62.4 | 17.4 → 10.8 | 276.0 → 244.1 | 327.2 → 292.4 |

All complete-model hashes match, including CPU runs and IRQ tuples. Active
task-indexing time sums the measured generator steps labeled
`Indexing task tracks…`; it includes more than just row grouping and excludes
scheduler waits. The inline fixture's longest step increased despite lower
total active work and readiness: allocation, native operations and GC remain
unbounded by the cooperative budget. CPU-fixture median longest observed Long
Tasks were 138 → 132 ms; the other three fixtures observed none in this window.
Neither result proves a hard frame deadline, a peak-memory reduction, faster
physical presentation or universal loading speedup. Repeated synthetic scans
can add yields even though the recorded-ID copying is gone.

The small source-rich kernel sample's preparation medians were 22.7 → 23.9 ms;
the mixed sample's were 36.0 → 32.8 ms. These tiny cases do not establish a
general speedup. Existing reports must be regenerated to embed the change.

Reproduce with the commands above and the `cpu` scenario documented in
[CPU preparation](performance-cpu-preparation.md). Range-specific regressions
cover recorded and synthetic-only input, frozen/duplicate synthetic IDs,
nonzero offsets, complete memberships, depth scans at 8,191/8,192/8,193 and
16,384/16,385 boundaries, and bounded per-step visits. A separate allocation
regression detects history-sized recorded-ID concatenations and fails against
the unchanged baseline. Existing cancellation/retry cases remain required.
Local measurements and validation logs are retained in
`build/track-row-preparation-audit/` (not release assets).

Validation for this follow-up passes 527 distinct tests, including 24 focused
preparation cases and three real captures. Optional-browser discovery also
passes 20 fixture/Node cases and cleanly skips the four browser cases without
Playwright. Both regenerated samples retain all five original JSON payloads
apart from build time; complete HTML matches after substituting the changed
runtime and normalizing generation metadata. All five regenerated public
screenshots are byte-identical.

## Text-to-Timeline line lookup

The first Text-to-Timeline jump builds its line index on demand. When the
recorded line span is at most twice the number of recorded calls, one typed
array stores both exact matches and the preceding-entry fallback. Exact
entry and exit matches still take priority; gaps never fall back to an exit
or to a synthetic frame. Sparse line ranges store sorted call ordinals
rather than allocating by line span. The density check stops as soon as
the span exceeds the limit.

In Chromium 145, on the 58,539 calls from `ping3`, cold construction plus an
exact lookup went from **3.0 ms** to **0.9 ms** (median); 93 subsequent gap
lookups went from **4.9 ms** to below timer resolution. With the same columns
repeated at distinct line offsets (468,312 calls), the cold median went from
**36.6 ms** to **6.6 ms**. Spacing line numbers 1,000 apart retained the sparse
path and its **4.7 ms** median for 128 gap lookups. These measurements cover
lookup work, not view setup, painting, or total jump latency. Local benchmark
and logs: `build/benchmarks/20261119-trace-jump/`.

The earlier sparse gap optimization recorded the minimum and maximum entry line
and the earliest maximum's ordinal per block of 256 calls. Subsequent gap
queries skip blocks that cannot contain a better predecessor. If the target
falls in the portion shared by every block's bounds, the lookup runs the
original linear scan instead. Exact matching and duplicate tie order are
unchanged. Those summaries used 12 bytes per block, in addition to the exact
map built on the first sparse jump.

In nine alternating Chromium 145 pairs at 4× CPU throttling, spreading the
`ping3` line numbers apart and repeating the columns eight times (468,312
calls) took **372.9 → 2.5 ms** for 256 warm gap lookups. The first gap took
**1.5 ms** in both versions. Shuffling those same calls took **1,221.4 →
1,230.3 ms** for the warm batch and **6.0 → 8.0 ms** for the first gap: the
optimization does not speed up highly overlapping blocks. The original
dense report's cold exact lookup stayed at **3.2 ms**. These are isolated
lookup measurements, not full UI jump latency. Benchmark and raw data:
`build/benchmarks/sparse-timeline-jump.DPQxYU/`.

The current sparse index replaces both that map and the block summaries.
It retains separate typed orders for entry and exit lines, reuses existing
order when monotonic, and uses stable radix passes for large unordered
columns. Both exact matches and gaps use binary searches. The latest
ordinal still wins an exact duplicate, while the earliest ordinal at the
preceding **entry** line still wins a gap. The retained indexes use four
bytes per indexed entry or nonzero exit; building them still needs scratch
space and runs on the caller's thread.

In a follow-up Chromium 145 comparison at 4× CPU throttling, building the
index and finding the first exact match in the 468,312-call sparse replay
went from **151.6 → 38.7 ms**. With shuffled calls it went from
**158.2 → 52.7 ms**, and 256 warm gap lookups went from **1,133.8 → 0.9 ms**.
The dense path is unchanged. These timings isolate lookups; the browser
still has separate rendering costs. Benchmark and data:
`build/benchmarks/sparse-line-cold.2EKJid/`.

## Cold call details

On first selection in a large track, recorded call navigation checks at most
256 positions per direction before waiting for the complete parent-link index.
It only enables a button early when it can prove the exact target. The time
breakdown and child preview still use the complete index; enabling every
button early does not bring that background work into an Overhead click.

On `ping3` (58,539 calls), eight alternating Chromium pairs at 4× CPU
throttling gave first-child button readiness medians of **81.9 → 15.1 ms**
and **78.6 → 14.8 ms** for slices 665 and 2366. The final navigation targets,
time breakdowns and child summaries matched. This measures the time from
canvas mouse-up until the button is ready in the DOM, not canvas paint time.
Benchmark and raw results: `build/benchmarks/20261122-cold-call-nav/`.

Replacing or closing the details pane now cancels any Timeline detail-work
timer whose frame callback already ran. A replacement queues its own frame
before running deferred work; if the old frame callback has not run yet, both
selections can still share it. This keeps a timer armed for an earlier call
from spending the background work budget before the replacement has had a
rendering opportunity. A browser regression holds the first timer across a
selection change and checks cancellation, the new frame, and the final result.

An experiment encoding missing call-tree links as zero removed the explicit
full-array fills but did not consistently improve readiness. Seven alternating
Chromium pairs at 4× CPU throttling measured synthetic cold navigation at
**91.7 → 98.9 ms** (450,002 slices); on the real `ping3` slice 665, cold
breakdown readiness went from **87.0 → 84.1 ms**, while settled warm readiness
went from **13.3 → 13.2 ms**. The original link encoding remains. The warm
benchmark now waits for the priming call's navigation, breakdown and child
summary to settle before starting measurement. Logs and preview:
`build/benchmarks/20261122-call-tree-links/`.

## Regression cases

`tests/test_timeline_preparation.py` covers empty/small input, frozen inputs, coalesced missing-depth extents and children, direct-child run termination, inline followed by untraced growth, deep single-call expansion, large scans, all uftrace space combinations, multiple tasks, negative deltas and equal-time ordering. Chunk sorting and merging are compared with the native comparator at 8,192/16,384 boundaries and partial runs. Already ordered rows must not invoke the rewrite path.

Node tests cancel during scans, copies and indexing and require replay to produce the same complete model. Browser tests cancel through the real Back to Text button and Escape, check that no partial canvas is mounted, then reopen against unchanged cached data; late resolution of a canceled request cannot overwrite the new build. Existing browser and data-provider tests retain coverage for source/navigation, filtering, overhead, inline mode, scheduling and cached-input reuse.

# Cooperative Timeline model preparation

Newly generated reports batch the model work behind the existing cancellable Timeline loading status. They keep the same complete model, drawing and input policy; they do not make reports use sidecar automatically.

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

## Regression coverage

## Measured responsiveness

Chromium 145.0.7632.6, three alternating fresh-context runs per side, medians
in milliseconds (before → after):

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

Focused preparation coverage passes all 13 tests. The isolated Timeline,
browser, provider and report suites run 324 tests with one fixture-dependent
skip. Real Pi4 and mixed fixtures pass 24 layout/theme/storage configurations
and 96 scope comparisons. The combined native-required worktree audit runs
964 tests: 960 pass, three skip, and one pre-existing pending Text-layout test
fails because Chromium reports `containIntrinsicBlockSize` with an `auto`
prefix. That independent Text change is not part of this iteration.

## Regression cases

`tests/test_timeline_preparation.py` covers empty/small input, frozen inputs, coalesced missing-depth extents and children, direct-child run termination, inline followed by untraced growth, deep single-call expansion, large scans, all uftrace space combinations, multiple tasks, negative deltas and equal-time ordering. Chunk sorting and merging are compared with the native comparator at 8,192/16,384 boundaries and partial runs. Already ordered rows must not invoke the rewrite path.

Node tests cancel during scans, copies and indexing and require replay to produce the same complete model. Browser tests cancel through the real Back to Text button and Escape, check that no partial canvas is mounted, then reopen against unchanged cached data; late resolution of a canceled request cannot overwrite the new build. Existing browser and data-provider tests retain coverage for source/navigation, filtering, overhead, inline mode, scheduling and cached-input reuse.

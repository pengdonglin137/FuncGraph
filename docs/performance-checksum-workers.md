# Parallel sidecar checksum verification

November 13, 2026. The provider reuses at most two lazily started SHA-256 Workers
for external resources of at least 1 MiB. Single-file reports remain Worker-free;
trace data, source positions, network chunks, cache policies and auto thresholds
are unchanged. Regenerate older reports to use the updated runtime.

## Ownership and integrity

- Sequential requests reuse one idle Worker. Overlapping requests reserve the
  least-loaded instance, creating a second only when the first is occupied.
  Reservations include startup and remain held until the checksum finishes.
- Original buffers transfer in both directions. Returned buffer size, digest
  shape, complete SHA-256 and cancellation are checked before decoding/cache.
- Canceling one subscriber preserves other subscribers. Canceling an isolated
  checksum can terminate its Worker without stopping its peer or a reserved job.
- A running Worker failure or malformed result stops the pool, rejects active
  jobs and prevents unverified cache entries. Retry uses native main-thread
  verification. Unsupported/startup-failed Workers retain full native checks.
- Two live Workers are a concurrency limit, not a bound on queued resources,
  buffers, full-model memory, DOM size or browser RSS.

## Windowed Text measurements

Baseline: `6e82139843ad`, changing only `assets/report/data.js` in otherwise
byte-identical sidecar resources. Chromium 145.0.7632.6, 1440 × 900, three
alternating independent-browser pairs per input, no CPU profiler and no parallel
tests/builds. Each run performs the same 17 sorting, scattered-window, folding,
filter and clear operations. Complete model and per-action visible/order hashes
are checked across all runs. These are two-animation-frame readiness proxies,
not frame-rate or peak-memory measurements.

Median readiness in milliseconds:

| Operation | 72,176 rows before → after | 371,997 rows before → after |
|---|---:|---:|
| Cold duration order | 610.9 → 451.6 | 1315.9 → 842.1 |
| Next scattered window | 649.6 → 518.3 | 1891.5 → 1481.8 |
| Return scattered window | 801.6 → 675.2 | 1289.3 → 1074.0 |
| Dense fold | 88.8 → 89.8 | 711.9 → 713.8 |
| Unfold | 1180.7 → 1222.1 | 1729.0 → 1552.1 |
| Filter | 424.1 → 390.9 | 690.0 → 611.8 |
| Clear | 56.0 → 101.4 | 179.9 → 186.4 |

Not every operation improves. Small-input Clear and Unfold regress in this run;
large scattered windows still wait roughly 1.5 seconds. Full-model work, parsing,
layout and garbage collection remain, and no automatic windowing is introduced.

## Default complete renderer

The same unchanged resources were also measured with the default complete Text
renderer, at 1500 × 900, in three serial alternating-browser pairs per input.
This comparison checks the full Timeline/Text model identity, rather than
extrapolating windowed improvements to the default renderer.

| Median metric | 72,176 rows before → after | 371,997 rows before → after |
|---|---:|---:|
| Complete Text ready (ms) | 2513.6 → 2525.3 | 16549.9 → 16297.0 |
| Longest observed task (ms) | 171.0 → 173.0 | 836.0 → 838.0 |
| Sampled renderer RSS (MiB) | 750.8 → 733.7 | 2582.5 → 2568.0 |
| Started Workers | 1 → 2 | 1 → 2 |

The complete renderer does not show a universal speedup: the small input is
slightly slower and the longest tasks remain. RSS is a sample, not a measured
peak or evidence of a total-memory bound. These small-run measurements are not
a statistical guarantee on other browsers or hardware.

Raw observations are under `build/benchmarks/20261113-text-checksum-pool/`:
`benchmark-{small,large}.json` retains every action, model identity, resource
count/byte observation and Long Task measurement. Inputs are the same as the
[allocation comparison](performance-text-rebuild-allocation.md).
`complete-{small,large}.json` retains all complete-renderer observations.

## Reproduction and regression scope

```sh
PYTHONPATH=.:tests python3 tests/benchmark_text_rebuild.py BEFORE AFTER \
  --trace TRACE --rounds 3 --scattered --folded --bidirectional --output window.json
PYTHONPATH=.:tests python3 tests/benchmark_text_delivery.py BEFORE AFTER \
  --rounds 3 --output complete.json
python3 -m unittest discover -s tests -p test_data_provider.py -v
```

Run measurements serially, using matching complete reports and independent entry
files; changing a hard-linked entry would also alter the comparison baseline.
The 62 provider tests cover the threshold, original-buffer transfer, ten pending
jobs over two Workers, idle reuse, same-Worker and peer cancellation, slot reuse,
startup reservations, pool failure, malformed replies, stale replies, Retry,
strict validation and the existing lazy-view/cache protocols.

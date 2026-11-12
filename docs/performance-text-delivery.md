# Verified sidecar delivery without main-thread checksums

November 12, 2026. Default single-file output, explicit auto thresholds, trace
records, syntax colors and analysis scope are unchanged. Regenerate reports to
use the updated provider; `fgview` supports its locally generated Blob Worker.
This improves delivery before the existing complete Text mount. It is not DOM
windowing, bounded model memory or a claim that large reports are finished.

## Evidence and implementation

The current 72,176-row Text-opening profile attributes about 1,068 ms to HTML
parsing and 795 ms to native `crypto.subtle.digest` calls on the main thread.
The provider already uses WebCrypto: replacing a nonexistent JavaScript SHA
implementation would not address this cost. A separate call/Promise timing
probe observes roughly 29–50 ms of synchronous work per 3–5 MiB resource on
this machine. These are diagnostic observations, not general WebCrypto timing
guarantees or a replacement for complete-ready measurements.

- Resources below 1 MiB keep the existing native main-thread checksum path.
  Larger sidecar resources share one lazily started Worker. Original buffers
  transfer to it, and the original bytes and digest transfer back. No full
  buffer copy or JSON model clone is introduced by this message protocol.
- Byte counts still match the manifest before checksum work. The main thread
  validates returned buffer/digest shapes and the expected SHA-256, checks
  cancellation, then uses strict UTF-8 decoding and the existing JSON parser.
  No unverified data is decoded or cached, including during Retry or fallback.
- Missing Worker/Blob support, construction/startup failure, unsupported worker
  crypto or a five-second readiness timeout keeps full main-thread verification.
  Failure after a transfer rejects active jobs; Retry re-fetches and verifies
  on the main thread. A broken worker is not repeatedly restarted that page.
- Request subscribers and worker jobs have separate ownership. Canceling one
  subscriber does not kill another consumer's shared request. Canceling a job
  leaves another resource's job intact. Canceling the last active job terminates
  the Worker; stale replies cannot cache bytes or resume an abandoned view.
- Text starts only the current and next network chunk, allowing next-chunk
  fetch/verification to overlap current HTML parsing. It does not issue a
  whole-trace `Promise.all`. Prefetch errors are handled immediately and raised
  when consumed, without an unhandled rejection during current-block parsing.
  The existing view signal cancels both requests, and all rows still prepare
  before one complete mount. Valid cached chunks remain reusable after cancel.
- The existing HTML cache remains independently limited to four chunks and
  16 MiB of verified bytes; source cache limits are unchanged. One resource may
  exceed the budget and load without caching. The lookahead bounds this Text
  pipeline's concurrency, not every request or memory allocation in a report.
- `fgview` adds only `worker-src blob:` to its CSP. Same-origin network access,
  path restrictions and remote-worker blocking remain. Embedded single-file
  resources do not start a Worker or gain a network dependency.

The pure analysis model, filtering, folding, ordering, source expansion,
selection, keyboard navigation and full line anchors retain their existing
implementations. JSON parsing, HTML parsing, model preparation, final layout
and GC are not moved into the Worker.

## Independent large-trace comparison

Baseline: `8499aaf06593`. Input: the same 72,176 original rows, 55,186 calls,
55,213 frames and 12 tracks, without vmlinux or source bodies. Input SHA-256:
`5954b07778c00623e0bb7324cd5e78130bb5188463ab7a3ca061eb89b2f1ab62`.
Both reports use `--no-config --no-btf --timeline --enable-fold --filter
--report-mode sidecar`. Three alternating pairs run through real `fgview` HTTP
in independent Chromium 145.0.7632.6 processes, 1500 × 900. No tests/builds run
concurrently with the measurements. The table uses medians, not guarantees.

| Observation | Before | After |
| --- | ---: | ---: |
| Complete initial Timeline ready, ms | 186.4 | 186.6 |
| Complete Text ready, ms | 3032.4 | 2526.0 |
| First parsed-status feedback, ms | 73.9 | 114.2 |
| Longest Text long task, ms | 174 | 176 |
| Text long-task blocking above 50 ms, summed ms | 124 | 126 |
| Sampled Text renderer RSS, MiB | 783.9 | 787.6 |
| Maximum concurrent Text chunk loads | 1 | 2 |
| Workers started | 0 | 1 |
| Text resources fetched | 18 | 18 |
| Mounted rows / paint blocks | 72,176 / 141 | 72,176 / 141 |

Complete Text is about 506 ms / 16.7% sooner for this fixture. First
parsed-status feedback is slower; initial loading feedback still appears
before parsing. The longest task and DOM-node count do not materially improve:
approximately 1.94 million nodes remain. RSS is sampled, not peak or a bound,
and status/two-rAF timings are feedback proxies, not input-to-pixel latency.
The source-free fixture does not establish source-rich performance, and small
reports may not benefit. A separate updated CPU profile observes no main-thread
digest samples during Text opening; parsing remains the largest measured task.

All six complete Text model hashes match, including every original row and
field, rather than only visible/filtered rows:
`bfcd594748ec635ac430b942433cff198a922f635af4ddf97573afe30fc1f9c6`.
Browser errors/CSP violations are zero. HTML caches release on successful
mount, and view reentry does not refetch Text. No record or analysis result is
dropped to produce the timing difference.

## Reproduce and verify

Generate each revision's report with the same input and options, then compare
them in alternating independent browser processes:

```bash
python3 funcgraph.py /path/to/traceB.txt --no-config --no-btf \
    --timeline --enable-fold --filter --report-mode sidecar --output REPORT_DIR
PYTHONPATH=tests python3 tests/benchmark_text_delivery.py BEFORE_DIR AFTER_DIR \
    --rounds 3 --output delivery-benchmark.json
PYTHONPATH=tests python3 -m unittest -v \
    test_data_provider test_textchunks test_viewer test_textviewport
```

The browser harness records worker count and maximum concurrent Text chunk
loads alongside complete model digests, request counts, RSS and long tasks.
Regressions exercise real worker transfers/reuse, concurrent JSON/text jobs,
the exact 1 MiB boundary, responses without streaming, unsupported/startup
fallback, native fallback integrity, malformed/wrong digests, UTF-8/JSON errors,
shared and independent consumer cancellation, terminal worker failure, late
replies and Retry. Chunk tests verify lookahead starts before current parsing,
full-row mounting, early prefetch failures, cancellation/reentry and preserved
folding/filtering/source/navigation behavior. Source-rich, single/sidecar,
light/dark and touch-sized fixtures are separate correctness checks, not the
large source-free performance benchmark.

The remaining priorities are still genuinely windowed DOM, presentation of
unmounted navigation targets, complete metadata preparation/retention, and
final layout/GC. This delivery optimization does not close those obligations.

The next [punctuation-node comparison](performance-text-symbols.md) uses this
Worker/lookahead runtime as its baseline. It removes redundant syntax spans,
not records, and reports its own independent measurements and residual DOM cost.

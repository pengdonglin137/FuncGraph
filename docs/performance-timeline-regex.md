# Timeline regex responsiveness

## Reproduced bottleneck

Cooperative indexing and batches cannot interrupt one native `RegExp.test()`.
In Chromium 145.0.7632.6, searching `^(a+)+$` against just one valid symbol
(`a` repeated 24 times followed by `b`) blocks the old input handler for
409.9 ms and delays a zero-delay timer to 411.5 ms. This is a finite synthetic
counterexample, not a claim that any particular captured trace contains it.
The baseline observation is retained at `build/timeline-regex/before.json`.
The issue is independent of call count; a large trace can expose the same
problem even when each distinct name is tested only once.

## Execution and fidelity

Find retains a synchronous path for literal regex atoms, optional leading `^`
and trailing `$`, escaped literal regex punctuation, and the universal `.*`.
These are still tested with the original native case-insensitive regex rather
than substituting string comparisons with different anchor/case semantics.
Other expressions, including long patterns, use an owned Blob Web Worker when
available. No user text is interpolated into worker JavaScript: query and
unmodified names are structured-cloned as data and compiled with the same
`new RegExp(query, 'i')` semantics.

The existing cooperative postings index is reused. Only its unique recorded
names are sent, in batches of at most 256 names and normally 65,536 UTF-16 code
units. One larger name is sent alone, never truncated. This is not a strict byte
or memory bound. Original calls, source pools, flags, arguments, raw compiler
suffixes and indexed call order remain in place; original typed columns are
not transferred out of the viewer. Returned boolean buffers and sequence IDs
are validated. Complete name hits are committed only after every batch, then
the unchanged sparse/dense postings merge and shared-filter intersection run.
Repeated navigation and filter-only updates reuse the complete cached query.

Escape, a new query, composition or view suspension aborts the request,
terminates its worker and releases its Blob URL. Old responses cannot publish
hits, counts or a queued navigation. Compilation errors retain **Invalid regex**;
runtime, message and protocol failures show **Search unavailable**, not zero
results or a partial subset. Retrying starts from complete original names.
No permanent control/status row or display redesign is added.

Missing Worker/Blob support, blocked construction or an early worker-load error
preserves the previous cooperative page-thread implementation. The
status can say **Searching in page…** and the input tooltip explains that a
long expression may block it. This compatibility path does **not** gain regex
isolation. Failures after the worker starts never silently rerun that expression
in the page. Worker startup has a five-second availability deadline; matching
has no automatic total timeout, so legitimate slow expressions are not turned
into incomplete results. The user can cancel instead. Index construction,
result merging, structured cloning and rendering still involve the main thread;
this is not a claim of zero jank, bounded memory or universally faster results.

## Paired measurement, 2026-11-16

Three alternating before/after pairs in Chromium 145.0.7632.6, using the complete
one-call input above, give these median milliseconds:

| Observation | Before | After |
| --- | ---: | ---: |
| Synchronous input/Enter dispatch | 414.9 | 1.1 |
| Zero-delay main-thread heartbeat | 418.6 | 1.2 |
| Complete search status ready | 414.9 | 425.0 |

The query still requires roughly the same computation and finishes slightly
later with worker startup/transfer overhead. The improvement is responsiveness
and cancellation, not faster regex evaluation. Each before run observes a main
Long Task overlapping the query (410 / 416 / 415 ms); none is observed in the
after query windows. This is not a zero-jank guarantee outside these windows.
Both sides settle to the same zero-hit outcome and retain the same complete
recorded Timeline digest.

The measured pair isolates only the two search JavaScript fragments: the before
entry embeds `search.js` and `interact.js` from `5dac94e15386`, and the after
entry embeds their current replacements in the same generated report. All
recorded Timeline/source payloads and other markup remain identical. This is
a finite synthetic frontend comparison, not a new capture or a large real-trace
throughput measurement. No other tests/builds run during this experiment.
Raw timings, complete report hashes and data fingerprints are retained in
`build/timeline-regex/paired.json` and `paired.log`.

An initial harness run filtered Long Tasks by start time only and omitted a
task that began just before the query timestamp. Its timer measurements are
retained in `paired-initial.json`, but its empty Long Task lists are not evidence
of no blocking. The corrected harness counts overlapping tasks and records
whether the Long Task API is supported; the table uses the corrected rerun.

## Reproduction and coverage

Create the finite input with one `function_graph` line:

```bash
python3 -c "from pathlib import Path; Path('/tmp/regex.txt').write_text('0) task-42 | ' + 'a' * 24 + 'b();\\n')"
python3 funcgraph.py --no-config --timeline --no-text-view \
  --output /tmp/after.html /tmp/regex.txt
```

Build the matching before report at revision `5dac94e15386`, using the same
input/options in a separate checkout. Then run, with no concurrent tests/builds:

```bash
python3 tests/benchmark_regex.py /tmp/before.html /tmp/after.html \
  --rounds 3 --output /tmp/regex-paired.json
PYTHONPATH=tests python3 -m unittest \
  test_timeline_regex test_timeline_search test_timeline_keyboard -v
```

The paired harness alternates order in fresh browser contexts, verifies complete
embedded Timeline/source payload equality excluding only `stats.build_time`,
and checks post-search Timeline immutability and UI outcome agreement. It records
all samples, source hashes, dispatch time, zero-delay timer latency, status-ready
latency and observed main-thread Long Tasks. Startup and post-timing verification
are excluded. Timer responsiveness is not physical presentation or a worst-case
bound; browser caches are not forced cold. The harness compares UI outcomes,
not every matching call; full hit-bit equivalence is separately covered by
worker regressions.

Tests cover native regex agreement for all names, Unicode, lookbehind,
backreferences, multiline names, long untruncated names, batching, repeated
query/filter caching, single/sidecar delivery, stale cancellation, composition,
view suspension, literal fast paths, absent/blocked workers, loader failures,
malformed later batches and runtime errors with retry. Chromium exercises real
workers and transfer buffers; this is not exhaustive browser/OS/CSP coverage.
Older HTML must be regenerated; the trace does not need recapture.

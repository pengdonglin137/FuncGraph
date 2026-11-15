# Responsive Compare filtering

## Reproduced issue

Compare previously called `RegExp.test()` synchronously inside table filtering
and repeated that work on each sort or overhead toggle. Cooperative row batches
alone cannot interrupt one expensive native regex evaluation. A finite example
is `^(a+)+$` against the valid symbol consisting of 24 `a` characters followed by
`b`. It blocks Enter even when the report contains just one function. This is
a synthetic frontend counterexample, not a claim about a particular capture.

## Interaction and data fidelity

Literal regex atoms, optional leading/trailing anchors, escaped punctuation and
the universal `.*` retain their immediate native case-insensitive regex path.
Other expressions use the same owned Blob Worker matcher as Timeline Find,
now shared in `assets/report/function-search.js`. Compare works independently
of Timeline and does not initialize its canvas or call index.

Names from measured and adjusted tables are indexed once, without stripping
compiler suffixes or truncating long names. Batches contain at most 256 names
and normally at most 65,536 UTF-16 code units; a larger name is sent alone.
Query and names are data messages, never interpolated into worker JavaScript.
The native `new RegExp(query, 'i')` semantics are unchanged. Complete returned
name hits are cached separately from table rows. Duplicate names in different
address spaces retain their distinct rows, and an adjusted table can have a
different row order. Sorting, changed-only and overhead changes reuse completed
hits instead of rerunning a potentially expensive expression.

While matching, an **only-when-needed** status says that the previous result is
still shown. **Cancel** or Escape restores the last completed expression and
table; editing, composition and leaving Compare also cancel outstanding work.
Workers terminate and their Blob URLs are released. Old/partial results cannot
replace the table, alter counts or trigger a queued CSV download. IME Enter is
left to the input method; filtering resumes after composition ends.

CSV is disabled while a filter runs. Clicking Export during the input debounce
flushes that input and waits for the entire match, then exports all matching
rows in the current sort/overhead state, not just the first 400 visible rows.
Invalid regexes and background failures retain the previous complete table,
show distinct actionable feedback and disable export until recovery. Enter
retries background failures; they are not misreported as zero matches.

## Compatibility and limits

Missing Worker support, blocked construction or a failure **before** the worker
starts uses the old page-thread regex semantics in cooperative name batches.
The temporary status explicitly says **Filtering in page… Complex expressions
may block**. This compatibility path cannot interrupt one `RegExp.test()` and
does not gain regex isolation. Errors after startup, malformed replies and
message failures never silently rerun the expression on the page thread.

Worker startup has a five-second availability deadline; successful matching
has no arbitrary total timeout or result cap. Cancellation is explicit rather
than silently dropping difficult names. Index creation, structured cloning,
complete-hit projection, sorting, rendering and CSV construction still involve
the main thread. This change does not promise bounded memory, zero jank or
universally faster completion, and does not change the shared Text/Timeline
expression filter.

## Paired measurement, 2026-11-16

Three alternating before/after pairs in Chromium **145.0.7632.6**, with the
single-function input described above, give these median milliseconds:

| Observation | Before | After |
| --- | ---: | ---: |
| Synchronous Enter/input dispatch | 422.7 | 1.1 |
| Zero-delay page-thread heartbeat | 425.5 | 9.6 |
| Complete filter result ready | 425.7 | 438.2 |

The result takes slightly longer because worker startup/transfer costs remain;
the improvement is page responsiveness and the ability to cancel, not faster
regex evaluation. Both sides finish with **0 of 1 functions** and retain the
complete original Compare payload. No tests or builds run during measurement.

The pair uses one generated single-file report, replacing only the Compare
script with its version from `68163a7d7e97` for the baseline. Both payloads have
SHA-256 `880f745f3ab3e42c2f9093f7ab403fa87d09dd6d19fa2a644c9022dee99a0cf4`.
The executable local probe, both complete reports, their hashes and the six
individual observations are retained in `build/compare-regex/measure.py`,
`before.html`, `after.html` and `paired.json` in that directory. This finite
measurement does not establish large-real-trace throughput or a frame-rate gain.

## Regression coverage

`tests/test_compare_regex.py` exercises complete native-regex matches over
multiple batches, duplicate user/kernel names, Unicode, newline and escaped
symbols, an untruncated 70,000-character name, adjusted-row reordering, cached
sorting, cancellation, stale replies, failure/retry, invalid syntax, IME,
pending/cancelled full-result CSV exports and the compatibility fallback.
It also checks real-worker heartbeat responsiveness and actual touch-coordinate
cancellation on an HTTP sidecar in both themes. Shared worker protocol and
Timeline behavior remain covered by `tests/test_timeline_regex.py`.

# Text ordering from complete records, not live rows

November 12, 2026. This moves time/task ordering, task headers and switch-gap
descriptions into the complete Text record model. It is a prerequisite for
rendering unmounted rows, not a claim that DOM windowing is implemented.
Regenerate existing reports; no new option, CSS or data schema is required.

## Model and projection

`fgTextModel.prepareOrder(model, mode)` prepares a DOM-free plan containing
the complete numeric row permutation, positioned header/gap descriptions,
group-hidden separator identities and switch-marker identities.
`fgTextModel.order()` provides the existing synchronous interaction path.
Completed time/task plans are cached by model identity; cancelled preparation
does not publish a partial plan. Preparation yields while scanning, summarizing
and ordering rows. A saved task order in sidecar is prepared before any Text
rows are mounted, within the existing cancellable staged loader.

The plan preserves the original behavior:

- Header/comment rows before the first task remain first. Untasked rows follow
  the preceding task; only untasked separator/blank rows inside a group are
  hidden. All original rows remain in the permutation.
- Tasks with more member rows come first; ties retain first-observed order.
  Idle identities remain per CPU and sort after non-idle tasks. Without PID,
  per-CPU groups still distinguish CPU 0 from a missing CPU.
- Headers, their full-trace line counts and CPU lists are independent of the
  current filter/fold state. Gap descriptions retain the original switch,
  resumed row, available timestamp difference and intervening real-row count.
- The raw record order, filter facts, source IDs, argument/return values and
  kernel/user address spaces are not mutated by presentation changes.

The current renderer projects the plan onto the existing row/source pairs in
bounded-size paint chunks. It validates complete membership before replacing
the content and updates navigation with the same numeric permutation. Header
text is assigned with `textContent`, including unusual task names. Cold
standalone ordering can prepare its model while the root is detached; it does
not depend on finding that detached root through `document.getElementById`.
Duration sorting keeps its existing separate presentation path.

## Independent real-trace comparison

Baseline runtime: `07aa64307092`, including punctuation compaction, Worker
checksums and one-chunk lookahead. Both reports use the same 72,176-row traceB
input, SHA-256
`5954b07778c00623e0bb7324cd5e78130bb5188463ab7a3ca061eb89b2f1ab62`,
with `--no-config --no-btf --timeline --enable-fold --filter --report-mode sidecar`.

Three alternating pairs each use fresh Chromium 145.0.7632.6 processes and
1500 × 900 contexts, through actual `fgview` HTTP. Each opens complete Text
from Timeline, then switches task/time four times. No tests or builds run
concurrently with timing. First-task/first-time rows use three observations
per version; repeated-task/repeated-time rows use nine each. Values are
medians; feedback means two rAF callbacks, not physical input-to-pixel latency.

| Operation / observation | Before | After |
| --- | ---: | ---: |
| First task order, synchronous ms | 527.6 | 357.9 |
| First task order, feedback ms | 587.7 | 414.5 |
| Repeated task order, synchronous ms | 495.9 | 301.7 |
| Repeated task order, feedback ms | 552.2 | 358.0 |
| First restore to time order, synchronous / feedback ms | 364.8 / 373.9 | 310.4 / 320.0 |
| Repeated restore to time order, synchronous / feedback ms | 354.8 / 363.2 | 302.8 / 317.4 |
| Repeated task order, longest long task ms | 496 | 301 |
| Repeated task order, summed blocking above 50 ms | 451 | 257 |
| Sampled renderer RSS after eight reorders, MiB | 1236.0 | 1128.2 |

Repeated task-order feedback is about 35% lower in this comparison. RSS is
sampled after the eight actions, before untimed fidelity hashing; it is not
peak memory, a limit or proof of bounded retention. Full Text ready also
measures 2446.1 / 2368.4 ms, but this is context, not a separate causal startup
speed claim. Results do not establish behavior on other machines or browsers.

All six complete raw model hashes match:
`bfcd594748ec635ac430b942433cff198a922f635af4ddf97573afe30fc1f9c6`.
Untimed complete-state hashes also agree across all runs, including every row's
identity, grouping/switch flags and visibility, each header/gap position and
text, paint-chunk row counts, and the navigation permutation. Task order has
all 72,176 rows, 12 group headers and 28 gap descriptions; time order retains
the same rows without headers/gaps. No browser error or CSP violation occurs.

## Fidelity and regression scope

Pure-model tests cover sparse identities, exact gaps, stable ties, idle/per-CPU
groups, missing/empty attributes, empty/header-only input, old index versions,
cache identity and cancellation in every preparation phase. Thirty seeded
random cases compare with an independent Python full-record reference.
Browser checks reject live row-text/task-attribute reads during cached
projection, verify saved-order preparation before mount, and fail closed on
incomplete mounted membership without discarding the remaining rows.
Existing source pairing, folding, filters, duration-sort restoration and
navigation checks remain applicable.

An additional source-rich mixed-report comparison covers single-file task/time
order, user-only and kernel/IRQ filtering, and clearing the filter in eight
real-browser contexts (light/dark × 390px touch/1500px desktop × before/after).
All 40 complete projected states agree, including every row's markup, address
space, classes/visibility, source-pair reference, headers, filter hits and
navigation. Seven real-kernel golden reports also retain their complete
normalized output after the exact JS asset updates. The 18 real-trace Text
chunks and full filter index remain byte-identical; both manifests and all
20 resources per report pass length/SHA-256 checks, and complete Timeline
data agree except diagnostic build time.

Both public reports are rebuilt using their original source settings and the
matching kernel commit `7475ea8b58616b33c04afb976cf0fbaad9dd63fb`. Their complete
normalized output is identical after only the exact three changed JS assets
are replaced in the baseline. All three public PNGs remain byte-identical.

The full suite runs 1,225 tests in 750.643 seconds: 1,222 pass and three
optional environment-gated checks skip, with raw, uftrace and sample-browser
checks required. Matching-vmlinux BTF and extra-real-trace checks pass
separately; the optional kernel-tool comparison remains skipped, not verified.
Existing unclosed-file/socket ResourceWarnings remain in the log.

Pure wheel, source archive and bundled-uftrace x86_64 wheel each install with
extras outside the checkout. Each runs 14 smoke checks with raw enabled:
pure/source correctly skip one bundled-recorder check, while native passes
all 14 with uftrace required. Twine accepts all three; changed source,
assets, tests and documentation are checked byte-for-byte inside the final
source archive. These are local validation artifacts, not a new release,
version tag or Pages deployment.

## Reproduce and remaining work

```bash
python3 funcgraph.py /path/to/traceB.txt --no-config --no-btf \
    --timeline --enable-fold --filter --report-mode sidecar --output REPORT_DIR
PYTHONPATH=tests python3 tests/benchmark_text_order.py BEFORE_DIR AFTER_DIR \
    --rounds 3 --output text-order-benchmark.json
PYTHONPATH=tests python3 -m unittest -v test_textmodel \
    test_browser.BrowserSmokeTest.test_text_order_by_task \
    test_browser.BrowserSmokeTest.test_text_order_keeps_chunks_bounded_and_source_pairs_intact \
    test_sidecar.SidecarBrowserTest.test_saved_task_order_is_prepared_before_any_text_rows_are_mounted
```

Reports, original input, benchmark JSON and verification logs are retained
locally under `build/benchmarks/20261112-text-order/`; large evidence is not
committed. The cache adds complete numeric plans and sets, not an overall
memory bound. This still prepares and retains the full Text model and DOM,
enumerates row identities and reparents every row/source pair on reorder,
and has substantial main-thread/layout work. Native group sorting is not
interruptible; yielding is not a hard time limit. Unmounted target rendering,
duration-sort metadata, source geometry, selection/copy/printing behavior and
viewport-bounded DOM remain required work before genuine windowing can ship.

## November 18: Full-DOM duration sorting

The mounted Text path reads each visible row's duration once, then sorts numeric
positions. Matching rows stay ahead of hidden ones; both equal-duration rows
and hidden rows retain their relative order. If the result already matches the
ungrouped display, the existing chunks stay in place. Otherwise rows move off
document and return with their source siblings and keyboard focus. Clearing
the sort restores the user's task/time preference. This change is separate
from the windowed Text renderer's ranking path.

The same 72,176-row traceB input was served through `fgview`, with complete
Text mounted before timing. The reports differ only in the embedded Text
runtime (`850441cab401` vs. the updated `assets/report/report.js`). Five
alternating before/after pairs used fresh Chromium 145.0.7632.6 processes at
1500 × 900. Medians below are in milliseconds; two-rAF feedback is a browser
timing proxy, not measured input-to-pixel latency.

| Operation, in order | Synchronous before → after | Two-rAF feedback before → after |
| --- | ---: | ---: |
| `sort:desc` | 984.0 → 540.1 | 1166.3 → 690.7 |
| Repeat `sort:desc` | 559.5 → 244.6 | 738.1 → 258.5 |
| `sort:asc` | 596.1 → 607.9 | 751.1 → 720.5 |
| Clear | 480.7 → 496.2 | 562.2 → 587.5 |
| `duration:>1 sort:desc` | 595.2 → 407.3 | 768.3 → 567.9 |

At every step, all 72,176 rows matched the baseline's order/visibility digest,
and keyboard navigation order matched the DOM. The final query kept 17,691
rows visible; the other operations kept all rows visible. The repeated query
retained its first chunk in all five updated runs and none of the baseline
runs. A browser regression also checks every chunk's identity, stable hidden
rows with unequal durations, source pairing and focus throughout sorting and
restoration.

The results do not show a consistent speedup for changing direction or
clearing. The timings still include walking the whole mounted DOM and updating
layout. No report data was removed, and this change does not bound full-DOM
memory. Local evidence: `build/validation/20261118-text-order/measure.py` and
`build/validation/20261118-text-order/duration-results.jsonl`.

### Restoring task/time order without a second layout pass

Clearing a duration sort, applying a query without the sort directive, or
applying a sorted query with no matches all restore the preferred task/time
order. This rebuild already refreshes Text layout and keyboard navigation.
The filter handler now uses that refresh rather than measuring the same
chunks and notifying fold guides a second time. Browser regression coverage
checks that all three actions refresh once, restore task grouping and retain
the exact row and expandable-navigation order for the resulting filter.

The same 72,176-row trace was also generated as a single-file report with
measured Text layout. Three alternating desktop pairs (1500 × 900, Chromium
145.0.7632.6) timed each action after sorting by duration, with only the
filter finalization changed. Median two-rAF feedback was 5358.6 → 4269.9 ms
for Clear, 2692.8 → 2065.3 ms for `duration:>1`, and 1886.5 → 1847.9 ms for
the zero-match query. Two-rAF feedback is not input-to-pixel latency; the
zero-match timings alone do not show a consistent improvement. A separate
390 × 844 before/after pair verified the narrow layout. All runs had one
layout refresh and fold-guide notification after the fix, down from two.
Complete row order, visibility, grouping, measured chunk style values and
keyboard navigation matched within each viewport. The remaining full reorder
is still expensive. Evidence: `build/validation/20261118-text-restore/results.jsonl`.

### Avoiding width reads for unchanged filtered chunks

Without a reorder, the mounted filter refreshes only chunks whose row display
or expanded-source state actually changed. A different width context (such
as a font, theme or viewport change) invalidates all measured widths. With
events hidden, changing the query still refreshes all chunks: event eligibility
can change without a corresponding display change. The measurement uses the
incoming filter's matches so it cannot reuse a width from the wrong event set.

The 72,176-row single-file report was compared again with only the Text runtime
replaced. Three alternating Chromium 145.0.7632.6 desktop pairs at 1500 × 900
produced these medians. The full test suite ran concurrently; two-rAF feedback
is a local browser timing proxy, not input-to-pixel latency. Chunk reads are
deterministic counts of native geometry reads on measurement chunks.

| Operation | Chunk reads before → after | Median two-rAF feedback, ms |
| --- | ---: | ---: |
| Repeat `sort:desc` | 141 → 0 | 1618 → 227 |
| Repeat `duration:>1 sort:desc` | 141 → 0 | 907 → 189 |
| Hide calls with `NOT func:^vfs_read$` | 141 → 32 | 1414 → 495 |

All six actions in each sequence retained identical row order, visibility,
measured chunk styles and horizontal extent, with keyboard navigation matching
the DOM. A separate 390 × 844 pair retained the same chunk-read reductions
and matched the narrow layout. First sorting and clearing after a reorder
still measured all 141 chunks; their costs remain. Evidence:
`build/validation/20261118-text-layout-pass/results.jsonl`.

### November 19: Reuse widths when restoring the previous order

Before a duration sort leaves a measured, unfiltered Text view with no folded
blocks, it saves the current chunk widths. On return to the same time/task
order, only chunks with the same row instances in the same order and the same
display state can reuse a width. The snapshot is consumed once; incompatible
layout context, event visibility, filters, folds or chunk membership fall
back to native measurement. Row/source pairing and navigation still rebuild
from the complete order. First duration sorting still measures newly formed
chunks, and the full Text DOM remains mounted.

The 72,176-row report was compared again with only the Text runtime changed.
Three alternating Chromium 145.0.7632.6 pairs at 1500 × 900 measured Clear
after each sort. Feedback is the median elapsed to two animation frames,
not physical input-to-pixel latency.

| Clear to chronological order after | Chunk reads before → after | Two-rAF feedback, ms |
| --- | ---: | ---: |
| `duration:>1 sort:desc` | 141 → 0 | 3581 → 621 |
| `sort:desc` | 141 → 0 | 3944 → 954 |

A separate 390 × 844 pair restoring task order reduced reads from 165 to 0.
Both sizes retained complete row order, visibility, grouping, chunk styles,
horizontal extent and keyboard navigation. Browser regression coverage changes
the font while sorted and verifies that Clear measures all restored chunks.
Evidence: `build/validation/20261119-text-order-widths/results.jsonl`.

### Sorting only matching rows and skipping empty width measurements

A duration sort compares only the visible rows, then appends hidden rows in
their existing order. Mounted layout also skips native width measurements for
chunks with no visible width-contributing content. Extra children and custom
chunk styles still take the native measurement path.

The same 72,176-row single-file report was tested in Chromium 145.0.7632.6 in
three alternating 1500 × 900 pairs and one 390 × 844 pair. All four pairs
matched full row order, visibility, chunk styles and horizontal extent;
keyboard navigation matched the DOM. The table shows desktop medians and
native chunk read counts, which were the same at both viewport sizes.

| Sorted query | Visible rows | Chunk reads before → after | Median two-rAF feedback, ms |
| --- | ---: | ---: | ---: |
| `duration:>1 sort:desc` | 17,691 | 141 → 35 | 2676 → 2704 |
| `func:^vfs_read$ sort:desc` | 7 | 141 → 1 | 1009 → 825 |
| `func:^not_in_trace$ sort:desc` | 0 | 141 → 0 | 357 → 202 |
| `sort:desc` | 72,176 | 141 → 141 | 4191 → 4332 |

Two-rAF feedback measures elapsed time through two animation frames, not
input-to-pixel latency. The seven- and zero-match queries were faster in every
pair, but the dense query, full sort and clearing showed no consistent timing
gain. Newly formed nonempty chunks still require native measurements, and
this change does not limit full-DOM memory. Evidence:
`build/validation/20261119-text-empty-chunks/results.jsonl`.

### Clear only refreshes chunks whose visibility changed

Clearing an unsorted filter now collects chunks whose row or source-panel
display changed while restoring visibility. The layout refresh still checks
the width context and measures every chunk after a font or viewport change.
When clearing a query with events hidden, it also refreshes every chunk for
the updated event eligibility. Clearing a duration sort still uses the
separate reordering and width-restoration path.

Three alternating 1500 × 900 Chromium 145.0.7632.6 pairs on the same
72,176-row report produced the following desktop medians. A 390 × 844 pair
produced the same read counts. Every action preserved full row order,
visibility, chunk styles and horizontal extent, with keyboard navigation
matching the DOM.

| Action | Chunk reads before → after | Median two-rAF feedback, ms |
| --- | ---: | ---: |
| Clear `NOT func:^vfs_read$` | 141 → 32 | 1377 → 448 |
| Clear again (already unfiltered) | 141 → 0 | 1483 → 80 |
| Clear `func:^vfs_read$` | 141 → 141 | 3472 → 3412 |

Two-rAF feedback is not input-to-pixel latency. The first two actions were
faster in all four pairs. Clearing the query that had hidden all but seven
rows still changes every chunk and showed no consistent timing gain. In a
separate instrumented run, native geometry reads accounted for about 2.8 s
of a 3.8 s full duration sort, so that path remains the larger bottleneck.
Evidence: `build/validation/20261119-text-clear-layout/results.jsonl` and
`build/validation/20261119-text-sort-breakdown/results.json`.

### Reuse measured row widths after duration reordering

The initial measured Text layout records intrinsic widths for generated rows
only when their maximum agrees with the browser's native chunk measurement.
New chunks formed by a duration sort can then take the maximum width of their
visible rows without laying out their contents. Missing measurements, changed
width context, active folds, hidden events and custom row styles keep the
native path. This adds a weak entry per captured row; it does not bound the
full-DOM memory footprint.

On the 72,176-row single-file report, three alternating 1500 × 900 Chromium
145.0.7632.6 pairs produced the following medians. A 390 × 844 pair had the
same read counts. Every pair matched full row order and visibility, measured
chunk styles and horizontal extent, with keyboard navigation matching the DOM.

| Action | Native chunk reads before → after | Two-rAF feedback, ms |
| --- | ---: | ---: |
| First `sort:desc` | 141 → 0 | 3929 → 1078 |
| Change to `sort:asc` | 141 → 0 | 3868 → 822 |
| Apply `duration:>1 sort:desc` | 35 → 0 | 2785 → 1578 |

Median synchronous Text initialization was 3234 → 3418 ms, including the
extra row reads but excluding HTML parsing. Two-rAF feedback is not physical
input-to-pixel latency. Browser regression coverage compares each chunk with
native geometry on desktop and touch layouts and confirms that a font-size
change forces native measurements. Evidence:
`build/validation/20261119-text-row-width-probe/final-results.jsonl`.

### Update only affected rows between sparse filters

On a large mounted report, consecutive unsorted filters with few matches
project the union of the old and new matches. The filter still evaluates the
full trace model, including graph predicates. Initial, dense, sorted, folded
or source-expanded states retain the complete DOM projection. Missing or
unexpected row identities also fall back to the complete path.

Three alternating 1500 × 900 Chromium 145.0.7632.6 pairs on the 72,176-row
report produced the following desktop medians. A 390 × 844 pair gave the
same row counts. All pairs matched the full row order, visibility, keyboard
navigation, chunk styles, horizontal extent and native width reads.

| Query transition | DOM rows visited before → after | Two-rAF feedback, ms |
| --- | ---: | ---: |
| `vfs_read` → `vfs_write` | 72,176 → 24 | 160 → 109 |
| `vfs_write` → no matches | 72,176 → 17 | 60 → 21 |
| No matches → `vfs_read` | 72,176 → 7 | 136 → 81 |

Two-rAF feedback is not input-to-pixel latency. Initial filtering and
transitions through a dense result still visit all rows and show no
consistent timing gain. Evidence:
`build/validation/20261119-text-filter-delta/results.jsonl`.

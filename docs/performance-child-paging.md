# Bounded direct-child browsing

The direct-child explorer used to append 100 rows on every Load more action.
Visiting a large parent's full population retained every visited row, even
though complete membership, aggregates, sorting and exports were already
available separately. Groups and individual calls now use reversible pages.

## Behavior and invariants

- Each mode retains at most 100 table rows. Previous / Next exposes every
  ordered member and reports the current interval/full total. Single-page and
  empty populations omit navigation rather than showing inactive controls.
- Groups and Calls have independent page positions. Sorting resets only the
  active mode. Literal Find, function scope and reset clear both. Shared-filter
  changes reset both only when the direct-child identity sequence changes;
  equivalent-set notifications preserve pages.
- Page state is independent of the current preview/aggregate cache. Overhead,
  Text round trips and inspecting child Details then returning to the same
  parent preserve pages. Exploring another parent resets scope and mode.
- Show/Longest still selects and reveals the real call without replacing the
  explorer. A selected call can be outside the current page; returning to its
  page restores its selected-row indication. Paging does not auto-select an
  unrelated row or change recorded positions.
- Both CSVs enumerate all sorted scoped rows regardless of displayed mode or
  page, preserving original call, trace-line, task, depth-gap and parent
  identities, scalar values and explicit timing/relationship bases. They do
  not advance pages or expand the table. Existing quoting/formula protection,
  count units and packed-position semantics remain intact.
- Table replacement retains the paging controls. At an endpoint, focus moves
  to an enabled direction. Paging scrolls the first row below the effective
  sticky header; compact/short windows keep their existing unpinned header
  behavior. Header/tools/footer stay horizontally reachable when the table
  overflows. Compact touch buttons are at least 44px high; Find uses 16px text.

Only displayed DOM is bounded. Complete child/group arrays, duration caches,
sorted rows and CSV buffers still scale with the parent's population. Comparing
an updated shared-filter scope scans that parent's identity sequence, not the
entire report, and only when the aggregate identity array changes. No general
first-open, total-memory or universally faster-paint claim is made.

## Top-Eight Preview

The call details preview scans the complete aggregate once, keeping only the
best eight groups in ranking order instead of copying and sorting all groups.
It still ranks by inclusive duration, then by first recorded call on ties. The
explorer and CSV use the full population, including groups outside the preview.

In a selection-only benchmark with 2,500 synthetic groups, the median of
10 interleaved runs went from 335 to 5 µs for mixed durations and from 25 to
7 µs when all totals tied. With strictly increasing durations it went from
26 to 42 µs: each subsequent group enters the preview, while the full sort
benefits from the already ordered input. These measurements exclude child
aggregation, rendering, and report startup.

## Reproduce

Generate single-file Timeline templates with identical options from parent
`1dccdc6c8db8` and this change, then run:

```bash
python3 tests/benchmark_children.py /tmp/before.html /tmp/after.html \
  --groups 2500 --rounds 3 --output /tmp/children-pages.json
```

The fixture has one parent, 2,500 functions, two direct calls and two excluded
grandchildren per function: 10,001 recorded calls in total. Every run traverses
all 2,500 groups and 5,000 direct calls, verifies their identities and excludes
grandchildren. Before/after order alternates between rounds. Timings cover
handlers and two-rAF intervals, not report startup, CSV or peak process memory.

### Measured result (2026-11-12)

Chromium 145.0.7632.6, 1500×900, medians of three runs:

| Metric | Groups before → after | Calls before → after |
|---|---|---|
| Visited records | 2,500 → 2,500 | 5,000 → 5,000 |
| Maximum retained table rows | 2,500 → 100 | 5,000 → 100 |
| Final panel elements | 27,541 → 1,143 | 60,039 → 1,241 |
| Page actions | 24 → 24 | 49 → 49 |
| Total page handlers | 33.0 → 42.3 ms | 85.7 → 89.1 ms |
| Total two-rAF intervals | 770.0 → 786.4 ms | 1,901.8 → 1,618.2 ms |

The demonstrated gain is bounded retained DOM without dropped records. Handler
totals increase slightly; group traversal is not faster in this run. Normal
rendering/layout costs remain, so these numbers are not a blanket speedup.
After timing, each run also exports both full populations without advancing
the displayed page. All six before/after group CSVs match byte-for-byte
(2,500 rows, 367,195 bytes), as do the six call CSVs (5,000 rows, 817,261 bytes).
The benchmark checks their SHA-256 values rather than assuming equal counts
imply equal content.

## Verification

Focused coverage includes full forward/reverse traversal, independent pages,
full exports from either mode, sorting/query/function/reset semantics, exact
page boundaries, empty scopes, changed/equivalent shared-filter sets, Overhead,
Details and Text round trips, a new parent, native keyboard endpoint focus,
and light/dark touch layouts at 320/390px portrait and 844px landscape. Existing
tests retain real-trace, depth-gap/grandchild exclusion, safe-label, count/packed
coordinate and aggregate-cache checks.

- Focused child-explorer suite: **12 tests pass**, 25.670s; six existing
  compatibility tests and six additional paging/state/touch tests. The initial
  new traversal test fails on the parent because paging is absent.
- Isolated parent-plus-change source: **298 tests pass**, 454.761s, covering
  existing browser behavior, duration/range/window/error/task paging and
  precise positioning. Pending Text work is absent from this isolated source.
- Required-native full worktree audit: **951 tests**, 557.203s; **947 pass,
  3 skip, 1 fails**. The sole failure is the known pending Text assertion
  `test_textchunks.TextChunkBrowserTest.test_native_layout_skips_offscreen_blocks_without_dropping_complete_records`:
  Chromium returns `auto …px` for some computed intrinsic sizes where that
  unrelated assertion expects bare `…px`. That work is preserved unchanged;
  this is not an all-green full-suite claim.
- JavaScript syntax, Python test/benchmark compilation and `git diff --check`
  pass. No dependency or public-sample changes are needed for these checks.

### Real-report compatibility

Generate full-Text reports from `tests/data/pi4_sched.txt` and
`samples/sample_mixed.jsonl`, with `--no-config --timeline --enable-fold
--filter` and single/sidecar delivery, from parent `1dccdc6c8db8` and this change.
The four model pairs are identical except for nondeterministic build time.

**24 configurations / 96 scope comparisons pass**: both traces, both delivery
modes, light/dark, and 1400×860, 390×844 and 844×390. Each configuration checks
unfiltered, actual shared-filter matching/empty scopes and uppercase literal
Find. Ordered group/call rows and both complete CSV byte streams match before
and after. Show selects the actual call without replacing the table; cold Text
round trips retain both mode contents. Use the real shared filter input (More
in compact layouts), not injected bitmaps. Fresh contexts avoid accumulated
download-rate state.

After scrolling wide tables horizontally, export/close remain reachable and
compact buttons are at least 44px high. No page errors, CSP violations or
document-wide overflow occur. The selected real parents have only 55 / 17
direct calls, so this is single-page compatibility evidence, not a real
large-parent speed claim; multipage completeness and bounds are checked by
the larger synthetic fixtures and benchmark separately.

## Documentation images

`docs/img/an_children.png` uses the complete Pi4 report (1,500 Text lines /
1,014 recorded calls) and shows all ten direct calls/functions of `path_openat`.
`docs/img/an_children_calls.png` uses the complete `samples/sample_io.txt`
report (739 lines / 485 calls), scoped to the two `__skb_try_recv_from_queue`
children of `__skb_recv_udp`. Both are light, 1400×1000; Notes is collapsed,
the split is arranged at y=500 and the view is zoomed to the parent. Single-page
footers omit unused navigation. They are arranged documentation views, not new
default pane heights or regenerated public HTML/captures.

No dependency pin, native payload, public capture, public sample HTML, version,
push or release change is part of this iteration.

## Repeated Find edits

Each mode can reuse its most recent unscoped order for the same data and sort.
Groups and broad Calls queries filter that order; a cold search or a narrow
Calls query can sort only its matching rows. Edits that change only case or
surrounding whitespace leave the current page and its DOM in place. Both CSVs
still use the original query string and retain the full sorted match set.
Reused panels also avoid measuring the unchanged heading after row replacement.

On Chromium 145.0.7632.6 with 40,000 synthetic groups and 80,000 direct calls,
`tests/benchmark_children.py BEFORE AFTER --find --groups 40000 --rounds 5`
gave these median input-handler times after preparing both orders:

| Scope | Groups before → after | Calls before → after |
| --- | ---: | ---: |
| At least one quarter of groups match | 8.6 → 2.7 ms | 8.6 → 5.0 ms |
| Fewer matches or no matches | 5.2 → 2.0 ms | 4.8 → 2.2 ms |

Match counts and the ordered visible rows matched across all benchmark queries.
These timings exclude startup, paint and export. The browser regression also
checks original CSV query text, full ordered matches, and focus/page retention
for equivalent edits.

When Find changes the match set but not the ordered rows on the current page,
the explorer retains those rows and updates the page count and export order.
Changed timing data or visible rows still rebuild the page. Pending orders
continue to clear stale rows. On real `ping3` slice 3541 (882 direct calls),
alternating `_` and an empty query left the same 100 calls visible. In an
844 × 390 Chromium 145 viewport, the median synchronous input handler fell
from 7.5 ms to 0.4 ms across 60 edits per version, with 100 rather than zero
rows retained per edit. This measures the handler, not startup or paint.
The 40,000-group synthetic benchmark (three runs per version) also reduced
dense Groups edits from 7.7 ms to 1.5 ms; sparse Groups and Calls timings
changed little, and all visible-row hashes matched. Raw results are in
`build/benchmarks/20261121-child-find/`.

## Build call-tree indexes per track

Recorded-call navigation, child previews and duration breakdowns share a lazy
index for each visited task track. Opening a call does not construct indexes
for other tracks. The index retains global slice IDs and uses track-local
positions for its three typed columns. Synthetic frames keep their own
navigation and do not request a recorded-call index just to find a target.

In Chromium 145, eight alternating pairs on a 400,000-call fixture indexed
only the selected 1,000-call track on first use. The three link arrays occupied
12,000 bytes instead of 4,800,000, and median index construction went from
3.4 to 0.1 ms. Navigation targets and the selected call's detail markup were
identical. These are index-level measurements, not total browser memory or
click latency. `ping3` has a single call-bearing track, so its index covers
the same 58,539 calls as before. Probe and samples are under
`build/benchmarks/20261119-trace-jump/`.

## Prepare large detail previews in batches

Collect direct recorded child IDs, aggregate their groups, and rank the top
eight cooperatively when the work exceeds the detail selection's 3 ms preview
budget. The budget is checked between batches, not between individual calls.
The explorer stays disabled while the preview is pending, then reuses complete
unfiltered aggregates for the same call and Overhead basis. The explorer's
first-page sorting and any filtered regrouping are separate work.

In five alternating Chromium 145 runs per variant at 4× CPU throttling, the
50,000-direct-call fixture (25,000 groups) reduced median selection-handler
time from 31.5 to 16.9 ms and the two-frame proxy from 61.8 to 52.6 ms. The
preview was ready later, at 69.5 vs 32.1 ms. On `ping3`, the largest direct-child
set (882 calls in `__zap_vma_range`) never deferred: handler medians were 15.6
and 15.8 ms. All sampled preview rows and the initial explorer page matched
exactly. These are programmatic-selection measurements, not physical display
latency. Reproduce with `tests/benchmark_children.py --preview`; raw logs are
under `build/benchmarks/20261120-child-preview/`.

## Reuse recent detail previews

Keep up to 128 previously visited calls' top-eight previews and counts, keyed
by call and checked against the current Overhead timing basis. Reopening a
cached call does not regroup its direct children. The cache retains neither
rendered DOM nor complete group and member lists. Leaves do not occupy a cache
slot, and the explorer and CSV still use complete data.

Four alternating Chromium 145 pairs at 4× CPU throttling revisited calls
after the initial selections. On a synthetic call with 60,000 direct
children, median preview-ready time dropped from 33.0 to 5.4 ms and the
preview no longer deferred on those revisits. For the largest child set in
`ping3` (882 direct calls), both versions rendered immediately, at 10.2 and
9.9 ms; that difference is small. Preview HTML and complete grouped CSV
hashes matched across versions. These are programmatic-selection timings,
not paint or end-to-end latency. Samples are under
`build/benchmarks/child-preview-revisit/`.

## Prepare large explorer orders without blocking the controls

The explorer gives each grouping, query or sort a short synchronous budget,
then resumes unfinished work in Timeline batches. The current request's
mode and sort headers remain usable. Paging and exports are disabled until
the current order is complete, and the panel doesn't show stale rows. A
new query, mode, sort, shared filter or Overhead choice replaces pending
work. Returning from another view resumes it; closing the panel discards it.

Five alternating Chromium 145 runs per variant at 4× CPU throttling on the
50,000-direct-call fixture (25,000 groups) reduced median explorer-open
handler time from 14.6 to 11.9 ms, with the first page ready at 24.3 ms
instead of 14.6 ms. The real `ping3` selection, with 882 direct calls in
12 groups, stayed synchronous (10.3 vs 10.7 ms). The top-eight preview and
first explorer page matched in both cases. A separate run traversed all
2,500 groups and 5,000 direct calls and matched both complete CSVs byte for
byte. Reproduce using `tests/benchmark_children.py`; raw logs are in
`build/benchmarks/20261120-child-list/`.

## Batch child CSV preparation and writing

If the other tab's order is not cached, the export gives it a short initial
budget and continues as **Ordering CSV…**. An already-ordered result with
fewer than 4,096 rows downloads directly; larger results use the shared
Timeline CSV queue, showing **Preparing CSV…** with coarse progress. Paging
does not replace the job. Switching views pauses work, and returning to
Timeline resumes it. A different list scope, timing basis or details panel
discards the old job. **Cancel CSV** never downloads a partial file.

Five alternating Chromium 145 runs per variant at 4× CPU throttling used
25,000 groups and 50,000 direct calls. Medians in milliseconds:

| Export | Click handler, before → after | Longest frame gap, before → after | Download ready, before → after |
| --- | ---: | ---: | ---: |
| Groups, cached order | 183.8 → 2.1 | 186.0 → 26.8 | 183.4 → 268.8 |
| Calls, uncached order | 428.8 → 4.9 | 431.6 → 26.6 | 428.0 → 564.1 |
| Calls, cached order | 387.8 → 1.9 | 393.7 → 23.4 | 387.3 → 509.7 |

The full Group and Call files retained the same sizes and SHA-256 digests;
Call files also matched between cached and uncached orders. On the real
`ping3` report, downloads for `__zap_vma_range` also matched byte for byte
(12 groups and 882 calls). Yielding adds to file readiness time to keep the
UI responsive; this is not a claim of faster download completion or bounded
total memory. Reproduce with
`tests/benchmark_child_csv.py`; raw results are in
`build/benchmarks/20261120-child-csv/performance.jsonl`.

## Share child summaries without duplicating members

The detail preview and grouped explorer share counts, first/longest IDs and
timing aggregates. Unfiltered calls reuse the recorded child IDs; a filtered
scope keeps only its matching IDs. Only a selective call listing that cannot
reuse a prepared order builds per-function member arrays. These arrays are
cached for subsequent queries in the same scope and duration basis. Ranking
ties, preview links and the complete CSV order are unchanged.

In seven alternating Chromium 145 pairs at 4× CPU throttling, selecting the
root of a synthetic trace with 50,000 direct calls in 25,000 groups added
5.60 → 3.34 MiB of post-GC JS heap relative to the pre-click snapshot.
Median mouseup handler time was 60.5 → 57.5 ms; the two-frame proxy was
77.4 → 75.9 ms, and opening the grouped explorer was 14.8 → 14.3 ms.
Three actual `ping3` selections with smaller direct child sets did not show
a consistent end-to-end speedup. A preview-only experiment saved more heap
but rebuilt aggregates when opening the explorer (14.7 → 37.2 ms on the
same synthetic trace), so that approach was not retained.

A separate run verified all 2,500 groups and all 5,000 direct calls through
paging, excluding grandchildren, with byte-identical complete CSVs. The
25,000-group Find comparison kept the same result counts and ordered visible
rows. Raw timings and parity results are under
`build/benchmarks/20261120-detail-subtree/`.

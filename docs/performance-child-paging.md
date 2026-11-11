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

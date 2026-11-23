# Compare: bounded table pages and compact controls

## Scope and reproduction

Compare previously rendered 400 functions, then appended up to 1,000 more on
each click of a table-cell prompt. Browsing a large result retained every old
row. The prompt was not a native keyboard-operable button. This was a browsing
and DOM-growth problem, not evidence that the comparison model was incomplete.

The replacement uses **400-row pages**. Previous / Next and a live row range
appear only when more than one page is needed. Each page replaces the preceding
rows; it does not truncate the model, filter again or sort again. New filters,
sorts, changed-only settings and overhead adjustments start on page one. View
roundtrips, resizing, unchanged-query Enter and exports retain the current page.
Paging resets vertical table scroll and retains horizontal position where the
new table width permits it. Delta bars use the complete matching population's
scale, including on the last page.

CSV still contains **all matching functions** in the current order, including
separate user/kernel rows with the same symbol. The original symbols, compiler
suffixes, address-space identities, numerical values and raw/adjusted payloads
are not rewritten. Exporting a new query waits for its complete result; pending,
invalid or failed queries cannot export a partial result. While a worker query
runs, users can browse complete pages of the previous result. Cancelling keeps
that page; a successful new query starts on page one.

## Compact layout and keyboard behavior

A first narrow-layout probe at **320 × 480** found a real boundary: after adding
the pager, the unchanged expanded toolbar placed its bottom at **572.5 px**,
outside the viewport. The final layout groups query, changed-only, sort and
overhead options under **Filters** at widths up to 700 px or with a coarse
pointer. Desktop controls remain inline without an extra desktop toggle.
Summary metrics and Export CSV remain visible; none are removed to make room.
The table height reserves the actual pager height. Touch paging, Filters,
Export and Cancel buttons have a minimum 44 px target height.
In both themes, the final collapsed **320 × 480** probe places the pager bottom
at **475.5 px**, within the viewport, without horizontal document overflow.

The dot on **Filters** indicates non-default settings or a query draft; the
accessible name includes “active”. Its position leaves the toolbar width
unchanged. Expanding and collapsing controls does not apply, clear or reset a filter or page. Very
short windows may require document scrolling when controls are expanded; the
toggle restores room for table browsing. This is not a guarantee that every
expanded control state fits every screen height.

Switching between wide and compact metric layouts, or resizing within the
compact layout, preserves the first visible row below the sticky header and
its within-row offset as far as the new layout permits. Compact values can
rewrap without rebuilding the current page. Returning to the wide layout also
restores its horizontal offset. An explicit page change still starts at the
top of the new page.

Native buttons support Enter and Space. At a page boundary, focus moves from a
newly disabled paging button to the enabled direction; if the pager disappears,
it returns to the table region. Narrowing a desktop moves hidden input focus to
Filters; widening moves a hidden Filters button's focus to the query. Cancelling
a worker with collapsed controls returns focus to the visible Filters button,
not a hidden input. Hiding controls also clears stale IME composition state,
so a missing composition-end event cannot disable ordinary Enter on reopening.

## Paired measurement, 2026-11-18

`tests/benchmark_compare_pages.py` traverses the same **10,000 distinct synthetic
functions** in fresh browser contexts using before/after reports generated from
the same `pi4_sched` and `pi4_default` fixtures. The baseline report predates the
change, at `68dc505d7bac`. Three alternating pairs in Chromium **145.0.7632.6**,
at **1280 × 720**, give these medians:

| Observation | Append baseline | Bounded pages |
| --- | ---: | ---: |
| Peak attached function rows | 10,000 | 400 |
| Peak elements inside the table region | 210,030 | 8,430 |
| Navigation actions to visit every function | 10 | 24 |
| Sum of navigation handler time | 1,388.1 ms | 915.1 ms |
| Sum of navigation through two animation frames | 2,099.8 ms | 1,281.9 ms |

Every run visits all 10,000 names in the same order and exports all 10,000 rows.
The complete model and CSV SHA-256 values match across all six runs; export
does not change the current page. Row counts select the function-name cells,
not the additional numeric cells carrying percentage tooltips.

The dependable improvement is **bounded attached table DOM**, not a universal
speed ratio. Appending exposes up to 1,000 new rows per action, whereas paging
exposes up to 400; these are not equal per-action workloads. Two-frame timing
includes frame scheduling, not full presentation or human reading time. The
test does not establish heap bounds, initial report-loading speed, capture
overhead or large-real-trace throughput. Complete rows, name indices, sorting
and CSV construction still consume resources proportional to the full result.
No other browser tests or builds ran during the final paired measurement.

To reproduce with a saved pre-change report and a new report of the same inputs:

```bash
python3 funcgraph.py --no-config --compare tests/data/pi4_default.txt \
    --output /tmp/compare-after.html tests/data/pi4_sched.txt
python3 tests/benchmark_compare_pages.py /tmp/compare-before.html \
    /tmp/compare-after.html --rows 10000 --rounds 3 \
    --output /tmp/compare-paging.json
```

The benchmark injects identical comparison data rather than treating the two
fixture captures as equivalent workloads. It requires Playwright and Chromium.

## Regression and artifact coverage

`tests/test_compare_paging.py` covers all pages in single-file and HTTP-sidecar
reports, both themes, zero/one/400/401-row boundaries, duplicate user/kernel
symbols, full-result bar scaling, sort/filter/overhead resets, complete CSV
exports, native keyboard navigation, focus transfer, scroll retention, worker
pending/cancel/success transitions and hidden-input IME recovery. Touch checks
exercise **390 × 600**, **844 × 390** and **320 × 480** without widening the
document. The existing browser/regex tests now assert page replacement rather
than accumulated rows. Contextual help explains paging and compact controls.

Both user guides and the implementation design are updated, and this note is
included in source distributions. Existing public kernel/mixed samples contain
no Compare view or Compare JS/CSS, so their HTML, PNGs and saved captures do not
need regeneration for this Compare-only behavior. The relevant guide Compare
screenshot is regenerated and checked separately. Existing reports that do
contain Compare must be regenerated to use the new paging and controls.

Raw measurements, before/after reports and hashes, rendered light/dark and
compact/expanded screenshots, regression logs and the final evidence inventory
are retained locally in `build/compare-paging-audit/`. Initial diagnostic failures
and superseded runs are retained but excluded from final passing test totals.

## Short-window table readability

Compare keeps up to 160px of table space available before letting the outer
page scroll. Short results do not grow to fill the allowance. The two header
rows now stick together when reading down the table, while the function
column remains pinned during horizontal scrolling.

On the 129-row kernel comparison, Chromium touch emulation at 320 × 240 and
844 × 240 measured 64 → 160px of table height and zero → five fully visible
data rows below the sticky header after scrolling. At 1280 × 720 the
table height and visible row count stayed at 530px and 24. None of these
viewports gained document-level horizontal overflow. The browser regression
also tests wheel scrolling on both report formats. These measurements cover
layout, not rendering latency or physical-device input. Evidence:
`build/validation/20261119-compare-short/`.

## Keyboard sorting and repeat-visit profiling

Metric headers sort with Enter or Space as well as pointer input. The active
header exposes its sort direction, and the sort-mode buttons expose their
selected state. Focus stays on the chosen header when the table is re-ranked.
On a compact-layout transition, the equivalent header keeps focus if it is
still visible; otherwise focus moves to the scrollable table region. Choosing
the already active metric or mode does not sort, replace rows or reset the
page. A different choice still ranks the complete result before replacing the
first page.

`tests/benchmark_compare_revisit.py` separates the first visit to page two
from returning to two already visited full pages. It uses the browser test's
1,207-row fixture, Chromium 145.0.7632.6 and matched hashes of the rendered
bodies in each viewport. In four alternating baseline/final pairs at
1280 × 720, the median repeat visit took 38.0 → 38.1 ms inside the click and
54.4 → 54.9 ms through two animation frames; these measurements do not show a
speedup for actual page changes. The baseline spent about 8.3 ms in the body
setter and 19.4 ms in layout on a repeat visit. The body setter includes
parsing and replacing nodes; CDP layout time spans both the handler and the
following frames. Frame intervals are not presentation latency.

Moving layout reads before row replacement lowered the synchronous click
duration in a prototype, but did not improve the wide first visit through two
animation frames (59.2 → 70.0 ms). Forcing experimental fixed column widths
with wrapping also increased layout time. Neither prototype is enabled, and
no page HTML or detached DOM cache was added. Raw baseline, prototype and
final measurements for wide and compact layouts are in
`build/validation/20261119-compare-revisit/`.

## Direct page navigation

On a multi-page result, the **Page** field accepts a page number followed by
Enter or **Go**. Previous / Next continue to work and update the field. An
invalid page leaves the current rows intact; Escape restores the current page
number. A jump uses the same completed ranking as incremental paging, renders
only the destination, and does not change CSV scope. While a filter runs, the
field refers to the previous complete result. If the next result fits on one
page, the form disappears and focus moves to the table if it was in the pager.

On the same 10,000-row fixture, three alternating Chromium 145.0.7632.6 desktop
pairs reached page 25 with **24 Next clicks and 24 table-body replacements**
before the change, or **one page submission and one replacement** with the
jump. Destination body hashes matched; both cases kept at most 400 rows
attached. Scripted median handler work was 877.2 vs. 42.1 ms for the full
journey, not a speedup for a single table render or a measurement of typing.
At 320 × 480 in touch emulation the added controls wrap below the row range;
for both fixture sizes the pager bottom stayed within the viewport (470.5px),
without document-level horizontal overflow. The table gave up 20.5px of
vertical space there. Probe, raw results and screenshots are in
`build/validation/20261119-compare-jump/`.

### Reclaim the compact table's count line

At widths up to 500px, multi-page results show the current row range in the
toolbar's existing count line rather than adding a footer line. If filtering
reduces the result, that line also shows the unfiltered total. A single-page
result keeps the usual matched/total count. The desktop placement is
unchanged, and the original live range announcement is still available to
assistive technology without exposing the compact visual copy twice.

Paired Chromium touch probes at 320 × 480 recovered **20.5px** of table height
for both 1,207 and 10,000 rows; at 390 × 600 the 10,000-row case recovered
**21px**. Both sizes fit **seven complete data rows instead of six** below the
header. The footer stayed within the viewport, without widening the document.
The 844 × 390 and 1280 × 720 controls showed no layout change. This measures
geometry, not rendering latency. Probe and screenshots:
`build/validation/20261119-compare-range/`.

### Reuse filtered rows when changing the ranking

Changing the metric or sort order reranks the rows from the completed filter
instead of testing every input row again. Compare retains a single population
in source order. A new accepted filter, a changed-only toggle, or a switch
between measured and adjusted rows rebuilds it. An unpruned result uses the
original array; a pruned result retains row references, not copied records.
This preserves source-order ties, separate address spaces, and complete CSVs.

In a 1,207-row regression, three sort changes went from **3,621 name tests
to zero** after the original query. The test also checks full CSV ordering
with ties in both measured and adjusted data. In Chromium 145, five
alternating before/after trials with 80,000 synthetic rows and 800 matches
brought median synchronous sort handling (including the table update) from
44.3 to 41.9 ms. The first filter measured 46.8 vs. 47.6 ms; this is not an
initial-filter speedup. The visible table markup after each reorder and the
complete CSV were identical. These timings exclude input dispatch and paint.
Probe and logs: `build/validation/20261119-compare-population/`.

### Reuse overlapping rows when redrawing the page

When the metric projection stays the same, Compare reuses DOM rows for the
records still in the current page and parses only records entering that page.
If the visible order is unchanged, no rows are reattached. Changed difference
bars move to the active column or rescale to the new population. A page with
no overlap or a different metric projection still renders a fresh body.
Only the currently attached rows are indexed; there is no detached-page cache.

The browser regression retains all 400 nodes when switching to a ranking with
the same order. It also checks a partly overlapping page, moved bars, and
rescaling after filtering. Five alternating Chromium 145 pairs on the same
80,000-row fixture (800 matches) give synchronous medians of **41.3 → 11.8 ms**
for a Calls sort that keeps the visible order, and **40.1 → 8.3 ms** for the
following Name sort. Across all six changes, including reorders with different
pages, the median is **41.5 → 40.3 ms**. The rendered body after each change
and the complete CSV match byte for byte. This does not measure paint or
end-to-end latency. Probe and logs: `build/validation/20261119-compare-row-reuse/`.

### Touch sorting and stable toolbar width

Compact **Filters** uses a native **Sort order** menu instead of the desktop
button strip. The query, sort menu and checkbox labels have 44 px touch
targets. Narrow phones put the query and sort in one row and the scope
checkboxes in the next. Switching layouts keeps the chosen order and routes
focus to the equivalent visible control (or **Filters** if collapsed).

In Chromium at 320 × 480, expanded controls with the overhead option visible
shrank from 110 to 94 px even though the sort target grew from 22 to 44 px.
Five complete data rows fit instead of four. Activating sort leaves Export
on the same toolbar line. The trade-off at 390 × 600 is one fewer complete
row (six to five) with controls expanded; 844 × 390 kept six. The desktop
layout was unchanged. These are geometry checks, not measurements of touch
latency. Screenshots and measurements: `build/validation/20261119-compare-compact-sort/`.

### Keep browsing while exporting a large result

Once the filter is complete, CSV preparation holds the current ordered row
array and time mode. More than 1,024 rows are formatted and encoded in chunks,
yielding to browser tasks between short slices. Paging and changing the report
controls do not mix new rows or modes into a file already being prepared.
**Cancel CSV** occupies the same toolbar slot as **Export CSV**, keeping its
focus and the table geometry at 320 × 480. Cancelling or leaving Compare
before preparation finishes discards the unfinished export. No partial file
is downloaded, and the user can retry.

Five alternating Chromium 145 runs exported 120,000 synthetic functions
(18,190,527 bytes). The median of the longest timer gap per run fell from
**221.9 to 20.6 ms**. Median time to initiate the download was **221.6 vs.
215.1 ms**. All exports had identical SHA-256 hashes. Timer gaps are a
responsiveness proxy, not input-to-paint latency; building the final Blob
still runs on the main thread, and download I/O was not timed.
Probe and logs: `build/validation/20261119-compare-csv-export/`.

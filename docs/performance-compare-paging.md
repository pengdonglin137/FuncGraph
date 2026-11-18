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

`Filters · active` indicates non-default settings or a query draft. Expanding
and collapsing controls does not apply, clear or reset a filter or page. Very
short windows may require document scrolling when controls are expanded; the
toggle restores room for table browsing. This is not a guarantee that every
expanded control state fits every screen height.

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

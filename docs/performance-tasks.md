# Bounded Timeline task browsing

November 12, 2026. This follow-up targets the **Tasks** detail panel, not report
startup or the complete large-trace pipeline. It applies to newly generated
Timeline reports, including event-only reports. Regenerate existing HTML to use
the new behavior.

## Behavior and evidence semantics

- **Previous** and **Next** replace pages of at most 100 task identities. Earlier
  pages do not remain attached. The status gives the current range and total;
  pagination controls are hidden for empty and single-page results. **Page**
  accepts a whole page number; press Enter or **Go** to jump. Enabled controls keep their
  DOM identity and keyboard focus during ordinary refreshes. At an endpoint,
  focus moves to the available direction instead of disappearing into the page.
- The current page survives reopening and refreshes that preserve the population.
  A new query, kind, sort, single-task scope or shared-match population starts
  from the first page. Existing literal matching and count/span semantics are
  unchanged; task identities are not deduplicated by PID.
- The pager stays at the bottom of normal-height panels. In very short panels,
  or when compact filters are open, it moves before the table and scrolls with
  the content so pinned elements do not cover the available space. It stays
  within reach during horizontal table scrolling.
- Touch paging, Show and export buttons have 44-pixel minimum heights. Compact
  layouts initially hide task search/filter controls behind **Filters** in the
  title bar, leaving room for actual rows. The toggle reports expanded state
  and indicates active conditions even while collapsed. In short panes, opening
  it scrolls Find into view; typing keeps the field visible, and Escape closes
  the controls and returns focus without discarding their values. Desktop keeps the controls visible. Resizing
  or hiding controls does not clear a query or change which identities match.
- **Export CSV** uses the complete current query/kind/matching/single-task scope
  and sort order, not the displayed page. Export neither advances the page nor
  attaches additional rows. Original indexes, names, flags, times and units
  remain unchanged, including formula escaping and unknown/open time bounds.

Task **Find** is a literal task-name/alias/PID/CPU query, not the toolbar's
function-name regex or the shared Text line filter. The shared filter changes
the matches column and optional **Matching calls only**, not raw call counts or
recorded task boundaries. In event-only reports, it remains a Text-only filter.

### Track navigation follow-up

November 19, 2026. **Show** restores the split when expanded details hide the
chart. It focuses the chart without changing the saved split or replacing the
Tasks page. Showing an already visible, expanded track does not rebuild the
track layout: three jumps in a 2,048-task fixture went from 6,144 writes to
track positions to zero. A first CPU jump to a scheduler-only task adds the
track after opening its task detail, requiring one layout pass instead of
two; later jumps to that visible track reuse its position. CPU jumps still
keep the time range, and **Show** still fits the selected task's extent.

### Reuse shared filter counts

Tasks uses the per-task counts already produced by the shared Timeline filter.
Applying a filter matching one exit per task in a 4,096-call, 1,024-task
fixture no longer rereads all 4,096 entries in the call-match bitmap. The
initial filter pass still checks entry/exit membership; a call is counted once
even if both lines match. If a new filter changes which calls match but leaves
all task counts unchanged, Tasks keeps its current page, rows and focus. A
changed count still resets the page, and CSV always names the current filter
even when the task rows did not need refreshing.

### Reuse task sort order while filtering the list

Find, Kind and Matching calls only narrow the active sorted list rather than
sorting their results again. Only the active sort is cached. The Matches
sort is rebuilt when task counts change; other sorts keep their order. In a
1,024-task browser fixture, six task-list updates under name sort went from
six sort calls to zero. The same test checks exact CSV order and that changing
only letter case or surrounding spaces keeps the current page and rows.

## Reproduce the comparison

Use two single-file Timeline reports generated with the same CLI options, one
from `4dbd9329bc15` and one with this change. Neither needs real source blocks;
the script substitutes identical synthetic data into each template:

```bash
python3 tests/benchmark_tasks.py before.html after.html \
    --tasks 10000 --rounds 3 --output tasks-benchmark.json

PYTHONPATH=tests:. python3 -m unittest test_task_paging -v
```

The fixture has 10,000 distinct kernel task identities, each with one recorded
point and no function calls. These are scaling fixtures, not real lifetimes or
new captures. Each measurement uses a fresh page in the same Chromium process;
report order alternates between rounds. The script opens Tasks and visits every
page through the native buttons, waiting for two animation frames between
clicks. It records every handler sample, frame interval, retained element count
and whether the initial pager is visible.

Two-rAF intervals include frame scheduling and are not a complete paint/TTI
measurement. JavaScript clicks do not model reading time or the extra scrolling
needed to reach the old offscreen Load more button. These measurements do not
cover report startup, parsing, CSV cost or peak memory.

## Measured result

The paging comparison, committed as `a86780633cbc`, uses Chromium `145.0.7632.6`, a 1500 × 900 viewport
and three alternating before/after rounds. Both templates use the same input
and CLI options, including `--timeline --no-text-view`; the substituted Timeline
models are identical. Times below are medians, not latency guarantees.

| Metric | Before: append rows | After: replace pages |
| --- | ---: | ---: |
| Initial attached task rows | 100 | 100 |
| Maximum / final attached task rows | 10,000 | 100 |
| Final elements inside the Tasks panel | 130,042 | 1,345 |
| Initial open handler | 19.8 ms | 18.6 ms |
| Sum of 99 paging handlers | 193.2 ms | 159.2 ms |
| Sum of 99 two-rAF paging intervals | 5,922.8 ms | 3,276.7 ms |
| Initial pager inside the panel viewport | No, all rounds | Yes, all rounds |

The deterministic improvement is the attached-row bound, not an overall
large-trace acceleration claim. The small open-handler timing difference is
not evidence of faster startup. Both implementations still visit every identity;
replacement pagination does not discard data from the model or export.

## Validation

- Eleven dedicated browser cases cover one/exact/partial pages, all 5,207
  identities without exceeding 100 attached rows, full CSV including unvisited
  pages and formula escaping, empty results, sorting, shared matches, cached
  facts, Show/reopening, keyboard focus and endpoints, sticky/short panels,
  compact filter state, resizing, both themes and sidecar CSP. These are included
  in a passing 48-test Tasks/event-only/lifecycle regression run.
- A saved QEMU Linux `7.4.0-rc2` scheduler-event projection retains 49 original
  events, 20 identities, 11 off-CPU intervals and 28 wake observations with zero
  function calls. Before/after Timeline models are identical except for the
  measured `stats.build_time`; all 20 CSV rows and every column are byte-identical
  across the old single-file, new single-file and new sidecar reports.
- Twelve real-report configurations cover single-file/sidecar × light/dark ×
  desktop/portrait/landscape. Native controls work without page errors or
  document-level horizontal overflow. Portrait shows actual task rows with the
  filters collapsed; landscape's very short panel remains scrollable rather
  than pinning controls over its content. Desktop, portrait and landscape
  screenshots were visually reviewed. Eight mobile configurations additionally
  exercise native Filters taps, the query, active-condition indication, Reset
  and scrolling an entire task row into view, including short landscape panes.
- The paging commit's required-native/sample-browser audit runs all 876 tests in 487.062 s:
  872 pass, three skip and one fails. The only failure is the previously
  documented, unrelated and uncommitted Text intrinsic-size assertion,
  `test_textchunks.TextChunkBrowserTest.test_native_layout_skips_offscreen_blocks_without_dropping_complete_records`:
  Chromium serializes intrinsic sizes with `auto`, while its assertion expects
  bare lengths. Original staged/unstaged Text changes are preserved; this work
  neither modifies that assertion nor claims a green full suite.

```bash
FG_RAW_REQUIRED=1 FG_UFTRACE_REQUIRED=1 FG_SAMPLE_BROWSER_REQUIRED=1 tests/run.sh
```

## Page jumps and short panes (2026-11-20)

On a 501-task Chromium touch fixture, with the detail pane set to 10% and the
table scrolled right, the vertical scroll needed to fully reveal Next fell
from **4,916 to 107 CSS pixels** at 320 × 700 and **4,876 to 56** at 844 × 390.
Coordinate taps turned the page in both layouts. Tests also cover direct
entry to the last page, invalid page numbers, retained focus, horizontal scroll
and identical complete CSV downloads. The real `ping3` preview has 18 tasks
and hides the unnecessary pagination controls. Local observations:
`build/benchmarks/20261120-task-pager/geometry.txt`.

## Cold open and on-demand task metadata (2026-11-20)

Tasks reuses the recorded per-track call intervals, CPU sets, and existing
128-call extent blocks instead of walking every slice again. Short tracks
read their recorded endpoints directly. Scheduling evidence still extends
the recorded call span and CPU list; open waits still mark a lower bound.
Display names, CPU text, and searchable alias text are cached when needed,
not built for every task just to render the first page. No work was moved
to Timeline startup.

Three interleaved A/B runs in headless Chromium 145 at 4× CPU throttling.
Measurements start with Timeline ready; times are medians in ms.

| Fixture | Click handler | First rows ready | Maximum frame gap |
| --- | ---: | ---: | ---: |
| 50,000 scheduler-only tasks | 119.5 → 80.0 | 119.5 → 80.5 | 147.1 → 105.6 |
| 500,000 calls on one task | 54.4 → 17.8 | 54.6 → 18.1 | 63.4 → 33.9 |
| Real `ping3` | 37.5 → 26.6 | 37.8 → 26.6 | 61.7 → 50.3 |

For each fixture the first page's rows and paging status matched, as did the
full CSV SHA-256. The first search still needs to format matching candidates;
the largest task population still has a noticeable cold-open frame gap.
`python3 tests/benchmark_task_open.py before.html after.html` reproduces the
comparison. Local results: `build/benchmarks/20261120-task-open/`.

## Cooperative CSV export (2026-11-20)

Exports below 2,048 task rows still download immediately. Larger exports use
the shared Timeline CSV scheduler with 128-row batches. The heading displays
progress beside **Cancel CSV** while browsing and Show remain available. Text
pauses the export; returning to Timeline resumes it. An export captures the
current order and CSV metadata; changing the query, task scope, sort or shared
filter cancels pending work, even when a metadata-only edit leaves all rows
unchanged. Closing or replacing the panel also cancels. No partial file is
downloaded.

Three interleaved A/B runs in headless Chromium 145, at 4× CPU throttling,
with 50,000 synthetic scheduler-only tasks. Medians after the panel is open
(ms); file-ready is the download trigger, not disk persistence.

| Fixture | Click handler | Maximum frame gap | File ready |
| --- | ---: | ---: | ---: |
| 50,000 tasks | 259.0 → 2.6 | 260.4 → 22.2 | 258.2 → 351.4 |
| Real `ping3` | 4.3 → 4.0 | 16.5 → 16.0 | 3.5 → 3.2 |

Large downloads take longer to become ready, but leave time for interaction.
Each fixture's exports matched by byte length and SHA-256 across runtimes.
The synthetic run does not measure first-open task aggregation. Reproduce with
`python3 tests/benchmark_task_csv.py before.html after.html` (use `--rows 0`
for the original capture). Local measurements: `build/benchmarks/20261120-task-csv/`.

## Remaining limits

Only the attached task-table rows are bounded. Full Timeline data, task facts,
matching counts and the sorted scope remain in memory; CSV traverses the full
scope and retains all encoded batches until download. The remaining task facts,
queries and sorting still scale with the recording. This is not worker-based or bounded-model
loading, and no overall large-trace speedup or memory ceiling is claimed.
Very short detail panes, including short landscape windows, still need vertical
scrolling or a larger pane; this change does not make every control and task row
simultaneously visible at arbitrary heights.
The later [detail expansion follow-up](detail-panel-expansion.md) adds an explicit
way to borrow the chart area without overwriting the saved split. The numbers
above describe the paging commit, before that additional control was introduced.

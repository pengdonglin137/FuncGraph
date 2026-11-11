# Bounded Timeline task browsing

November 12, 2026. This follow-up targets the **Tasks** detail panel, not report
startup or the complete large-trace pipeline. It applies to newly generated
Timeline reports, including event-only reports. Regenerate existing HTML to use
the new behavior.

## Behavior and evidence semantics

- **Previous** and **Next** replace pages of at most 100 task identities. Earlier
  pages do not remain attached. The status gives the current range and total;
  both directions are disabled for empty results. Enabled controls keep their
  DOM identity and keyboard focus during ordinary refreshes. At an endpoint,
  focus moves to the available direction instead of disappearing into the page.
- The current page survives reopening and refreshes that preserve the population.
  A new query, kind, sort, single-task scope or shared-match population starts
  from the first page. Existing literal matching and count/span semantics are
  unchanged; task identities are not deduplicated by PID.
- The pager stays at the bottom of normal-height panels without requiring a
  scroll past 100 rows. Very short panels use ordinary scrolling for both the
  title and pager so pinned elements cannot cover all available space.
- Touch paging, Show and export buttons have 44-pixel minimum heights. Compact
  layouts initially hide task search/filter controls behind **Filters**, leaving
  room for actual rows. The toggle reports expanded state and indicates active
  conditions even while collapsed. Desktop keeps the controls visible. Resizing
  or hiding controls does not clear a query or change which identities match.
- **Export CSV** uses the complete current query/kind/matching/single-task scope
  and sort order, not the displayed page. Export neither advances the page nor
  attaches additional rows. Original indexes, names, flags, times and units
  remain unchanged, including formula escaping and unknown/open time bounds.

Task **Find** is a literal task-name/alias/PID/CPU query, not the toolbar's
function-name regex or the shared Text line filter. The shared filter changes
the matches column and optional **Matching calls only**, not raw call counts or
recorded task boundaries. In event-only reports, it remains a Text-only filter.

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

The final-source comparison uses Chromium `145.0.7632.6`, a 1500 × 900 viewport
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
- The final required-native/sample-browser audit runs all 876 tests in 487.062 s:
  872 pass, three skip and one fails. The only failure is the previously
  documented, unrelated and uncommitted Text intrinsic-size assertion,
  `test_textchunks.TextChunkBrowserTest.test_native_layout_skips_offscreen_blocks_without_dropping_complete_records`:
  Chromium serializes intrinsic sizes with `auto`, while its assertion expects
  bare lengths. Original staged/unstaged Text changes are preserved; this work
  neither modifies that assertion nor claims a green full suite.

```bash
FG_RAW_REQUIRED=1 FG_UFTRACE_REQUIRED=1 FG_SAMPLE_BROWSER_REQUIRED=1 tests/run.sh
```

## Remaining limits

Only the attached task-table rows are bounded. Full Timeline data, task facts,
matching counts and the sorted scope remain in memory; CSV still traverses and
constructs the complete export in memory. Initial facts, queries and sorting
still scale with the recording. This is not worker-based or bounded-model
loading, and no overall large-trace speedup or memory ceiling is claimed.
Very short detail panes, including short landscape windows, still need vertical
scrolling or a larger pane; this change does not make every control and task row
simultaneously visible at arbitrary heights.

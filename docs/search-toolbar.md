# Timeline search controls on narrow screens

## Reproduced overlap

In a 568 × 320 touch window, the previous published mixed report could shrink
the search group to 51.38 px while its input still required 100 px and its
navigation pair required 90 px. The overflowing input and Next button were
painted underneath Full screen. A button could therefore be inside the viewport
and still be unusable. Checking only document overflow and target size did not
detect this collision.

The problem occurs before searching as well as after searching `.*`. It is a
layout error, not a missing match or delayed search result. The recorded calls,
query semantics and selected-call indexes do not need modification.

## Layout change

Compact search now has a 194 px flex basis: the existing 100 px input minimum,
90 px navigation pair and 4 px gap. Its minimum width follows its content rather
than permitting a wrapper narrower than its visible children. The existing
flex layout can move the quick-action group to the next row instead of placing
neighboring actions over the input or navigation buttons.

Exact position/total digits and their nonbreaking numeric pair remain intact;
the `calls` unit may wrap as before. The input can grow when room is available,
and navigation/full-screen touch targets retain their sizes. No buttons, extra
status rows, alternate query engine, ellipsis or rounded counts are introduced.
Noncompact styling is unchanged. Toolbar size changes continue to refit the
existing [Tools and Notes panels](toolbar-overlays.md).

These are CSS sizing constraints, not new pointer handlers or trace scans.
They do not change filtering, search results, call names, raw arguments,
source information or export data. Reduced toolbar height in an affected
window is usable layout space, not evidence of faster parsing or rendering.

## Verification

```bash
PYTHONPATH=tests python3 -m unittest test_search_toolbar test_timeline_search -v
```

The new browser suite uses mixed-report fixtures with inline and Notes controls
present, both themes, single-file and HTTP sidecar delivery, initial/active/
invalid/empty/cleared search, narrow and landscape touch windows and a roomy
desktop. It checks every visible input/button at its center and four inset
corners, pairwise non-overlap and containment in the search group, rather than
only viewport bounds. Raw touch coordinates verify that Next/Previous really
navigate and never accidentally activate Full screen.

Long counter fixtures preserve the complete numeric pair through 32-bit maximum
values; these are explicit layout fixtures, not claims about captured trace
size or real searches over billions of calls. Orientation and open-menu checks
retain the query, selection and complete Timeline data. Tests exercise Chromium
touch emulation, not every physical browser or accessibility configuration.
Older reports need regeneration; captures do not need to be repeated.

The focused related run passes all 106 cases without skips, including the five
new layout/pointer tests and the existing search, background regex, keyboard,
menu, filter, detail-action and help suites. Its result is retained locally in
`build/search-toolbar/related.log`; before-fix collision observations are also
retained. The tests are finite browser/layout checks, not trace-throughput or
physical-device latency benchmarks.

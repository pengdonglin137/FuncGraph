# Timeline search controls on narrow screens

## First search and repeated navigation

Simple input waits 90 ms instead of 150 ms. Expressions routed to a Worker
keep the 150 ms delay; Enter and Shift+Enter bypass both timers. The lazy
postings index uses a typed counter table when the string ID range is no larger
than the recorded call count. A sparse ID range uses the existing Map path.
Both keep distinct names in first-seen order and the same call order within each
name. Index construction still yields between batches; cancelling a query
does not publish a partial index or partial matches.

Offline Chromium 145, `ping3` (58,539 calls, 1,570 recorded function names),
`^x2apic_send_IPI$` (13 matches), 7 fresh-page samples per case. The comparison
changes only the two Timeline search scripts. Values are median milliseconds.

| Phase | Before | After | Before, 4× CPU throttle | After, 4× CPU throttle |
|---|---:|---:|---:|---:|
| Input → matches ready | 154.2 | 93.3 | 168.9 | 100.0 |
| First Enter → publish results, before jump | 4.3 | 3.5 | 20.3 | 14.2 |
| First Enter → canvas starts drawing | 17.6 | 17.7 | 58.1 | 56.2 |
| Repeated Enter → canvas starts drawing | 16.9 | 16.9 | 24.9 | 26.9 |

Canvas drawing start is not paint completion or end-to-end display latency. No
meaningful improvement is established for redraws or repeated navigation.
A separate timing probe identifies opening the call details as the largest
synchronous part of this particular first jump: about 8 ms at native speed
and 35 ms under 4× CPU throttling. This is not a measurement of all slices.
`node tests/benchmark_search.js` separately measures model CPU: at one million
calls and 1,000 names, median index construction drops from 23.5 ms (Map) to
7.6 ms (typed), with identical postings and about 4 MB of retained typed data
in both cases. Transient allocation and browser heap are not measured by that
benchmark. Browser observations and the harness are in
`build/benchmarks/20261121-search-latency/`.

## Narrow-screen layout

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

The final broader UI regression run passes all 694 cases without skips in
767.669 seconds. It includes the five new cases plus existing browser,
search/input, menu, filter, detail, hover, saved-view, resize, mixed-sample,
Text/event/layout, source and single/sidecar report coverage. These counts
overlap with the focused run and are not additive. The full result is retained
in `build/search-toolbar/regressions.log`; this is not a rerun of every
unrelated backend/parser test in the project.

## Published examples and package verification

Both examples are regenerated from their existing captures with the matching
Linux `v7.4-rc2` image and source at
`7475ea8b58616b33c04afb976cf0fbaad9dd63fb`. Original Text row contents and all
attributes, event markers, source pools/maps, Timeline identity and complete
Timeline payloads remain equal to the previous examples; only regenerated
`stats.build_time` is excluded. The kernel/mixed reports retain 739 / 1,504
rows, 485 / 714 recorded calls and 10 / 74 event rows. No recapture or trace
prefiltering is involved.

Actual Chromium observations after searching `.*` in the complete examples:

| Example | Touch window | Previous toolbar height | Updated toolbar height |
| --- | --- | ---: | ---: |
| Kernel | 568 × 320 | 134.59 px | 102 px |
| Kernel | 640 × 360 | 134.59 px | 102 px |
| Mixed | 568 × 320 | 151.59 px | 119 px |
| Mixed | 640 × 360 | 151.59 px | 119 px |

At 568 px, the search group's width changes from 51.38 to 459.77 px; at 640 px,
from 123.38 to 531.77 px. In both original 568 px examples, a real touch at Next's
center enters full screen and leaves call index 0 selected. Corrected reports
instead select call 1, remain outside full screen, and return to call 0 with
Previous. Every visible control passes center/four-corner hit testing without
pairwise overlap, before and after searching. The same checks also pass inside
full screen, with query and selection preserved when exiting.

The roughly 32.59 px reduction moves existing content upward; it does not
increase the measured canvas height in these short-window cases, nor reduce
the detail height. This is a layout/readability improvement, not a throughput,
memory or frame-rate measurement. All seven regenerated ordinary public/guide
screenshots remain byte-identical, so no PNG asset update is necessary.

The portable `0.9.4` wheel and source distribution build successfully. Isolated
installed smoke executes 14 cases: 10 pass and four optional native uftrace/raw/
extra-dependency cases are skipped because those payloads were not requested.
A report generated by the installed CLI from `tests/data/pi4_sched.txt` also
passes the initial/active/full-screen hit-testing and raw Next/Previous touch
checks. Complete Timeline JSON remains unchanged throughout these probes and
no browser page errors occur. This does not validate native-recorder or
raw-enabled wheels.

The actual source archive includes both this guide and `toolbar-overlays.md`,
matching their source bytes; the wheel contains the exact current Timeline
CSS. Local fidelity fingerprints, before/after screenshots and probe/build
results are retained in `build/search-toolbar/`, including
`sample-fidelity.json`, `rendered-reports.json`, `mixed-before.png`,
`mixed-after.png`, `package-content.json`, `installed-smoke.log` and
`screenshots.log`.

## Stable layout while refreshing search

Once Find has matches, updating the expression keeps the navigation controls
and the counter's space in the toolbar. The toolbar shows a pending indicator
instead of the old count. Previous and Next are unavailable while refreshing:
they keep existing focus without taking a Tab stop or queueing a jump. If
the completed query has no usable matches, focus returns to Find when it
was on one of those controls. The first search and cleared state still hide
unused navigation buttons in compact mode.

In Chromium touch emulation at 320 × 420 and 720 × 420, starting text
composition after a completed query previously moved the top of the canvas
up by 32.6 px. The paired probe now shows no movement. The regression also
checks 350 × 420 and 760 × 420 with single-file and sidecar reports. These are
layout measurements, not measurements of physical-device input latency or
search throughput. Logs: `build/validation/20261119-search-refresh-layout/`.

## Compact portrait controls

At up to 420 CSS px wide on touch screens, Timeline uses shorter labels and
smaller horizontal padding without reducing the 44 × 44 px touch targets.
The Notes count stays in the accessible name on the narrowest screens.
When a shared filter is active, Tools shows a dot rather than widening the
toolbar; its accessible name still says **Tools · Filter on**. Controls can
wrap when the available width or text size requires it.

On standalone `ping3` in Chromium touch emulation, the idle toolbar measured
150 → 102 px at 320 × 640, 360 × 800, 390 × 844 and 420 × 844. The chart gained
48 px in each case. At 320 × 640, a search with 55 matches measured 183 → 102 px.
The 568 × 320, 844 × 390 and 1500 × 900 layouts were unchanged in the idle
comparison. Native taps exercised search navigation, Notes, Inline and
full screen. Logs and screenshots: `build/benchmarks/compact-call-audit/`.

## Keep search and filtering on one row in more landscape sizes

On touch layouts up to 800 × 480 CSS px in landscape, an active shared
filter uses the same compact dot as narrow portrait. The Tools button still
has the accessible name **Tools · Filter on**, and wider landscape layouts
still show the text. The change leaves the query, matches, and touch target
sizes alone.

With Find active on `ping3` in Chromium touch emulation, turning on a filter
at 660 × 360 no longer expands the toolbar from 54 to 102 px. In page full
screen at that size, the chart viewport grows from 144 to 192 px. At 720 × 400
outside full screen, the chart grows from 114 to 147 px. At 568 × 320 the
change moves the chart up without growing its already-minimum height.
The 11-size sweep found no blocked search or toolbar targets. Logs:
`build/benchmarks/20261121-short-toolbar/`.

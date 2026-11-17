# Timeline menus in short windows

## Reproduced problem

The Tools dropdown previously limited its height to a fraction of the whole
window, without accounting for its starting position below the toolbar. A
wrapped Find counter or a short landscape window could leave the last actions
outside the viewport even after the dropdown itself was scrolled to the bottom.

In the previous published mixed example, at 568 × 320 touch dimensions with
`.*` searched, the menu started at 259.09 px and ended at 412.69 px. Tracks,
Tasks, Go to, Saved views and several analysis controls were partially or
entirely outside the 320 px viewport. At 640 × 480 desktop dimensions, the
menu ended at 523.5 px. These are finite browser layout observations, not
physical-device or performance measurements.

## Placement and interaction

Tools retains its normal dropdown position whenever it fits. Otherwise, the
menu uses the available space below the toolbar or above its own opener,
whichever provides more usable room. If neither side can contain a control,
the panel uses the visible viewport instead of becoming a tiny inaccessible
strip. An 8 px viewport gutter is retained. Existing width, color, typography,
touch controls and preferred height caps remain; overflowing content scrolls
inside the panel. Compact Notes uses the same placement rules. Desktop Notes
remains the existing inline strip.

Floating panels paint above the sticky Text/Timeline tabs, so opening upward
does not trade offscreen clipping for controls covered by the tab bar. Existing
help/dialog layers remain above them. Escape, outside dismissal, focus return
and full-screen behavior are unchanged; no new button or permanent row is added.

Opening a panel, changing the toolbar/panel size, page scrolling or resizing the
visible viewport updates placement. Vertical `visualViewport` bounds are used when
available, with `innerHeight` as the compatibility fallback. This accommodates
reported keyboard/viewport changes but does not promise identical behavior
across every mobile browser or physical keyboard configuration.
This does not introduce a new horizontal placement or pinch-zoom policy.

Only the small open panel and its anchors are measured. Closed menus do not
measure overlay geometry on scrolling or pointer movement. Updates are
coalesced with animation frames; a stable open panel does not continuously
re-measure on hover. Re-measuring restores the panel's scroll offset after
temporarily checking its preferred height. Trace records, source data,
selection, time range, filter population and canvas/detail allocation are not
changed to make room for a floating menu.

## Reopening and keyboard focus

A November 18, 2026 Chromium audit found that Tools retained its scroll offset
but always focused the first input, even when it was outside the visible panel.
In the published mixed example at 568 × 320, reopening at the bottom left the
filter input at y = −353…−309 while the panel occupied y = 8…153.5.

Tools now keeps that reading position and focuses the first fully visible,
available native control in logical order. A partly visible control is used
only when no complete control fits; only the menu scrolls enough to reveal it.
If the visible region contains explanatory text but no control, the dialog
itself receives focus without jumping back to an offscreen field. Its negative
tab index does not add a permanent Tab stop.

Selects and disclosure summaries participate alongside inputs and buttons.
Disabled, inert, CSS-hidden and negative-tab-index controls are skipped.
Collapsed disclosure contents are excluded explicitly, including nested
disclosures: a nonzero layout rectangle alone does not establish that a control
is painted or focusable. Their visible summaries remain eligible.

Arrow, Home/End and Page Up/Down keys inside Tools do not pan, fit or navigate
the underlying trace. Native editing and selection in inputs/selects still
work. Escape retains its existing close/draft-restoration behavior, and the
existing full-screen/help shortcuts remain available from buttons or the panel.
Opening Tools neither applies a filter draft nor loads deferred Text/Compare.

Focus selection inspects only the small Tools subtree on opening, not calls or
Text rows. Stable hover and closed-panel paths do not gain geometry reads or a
new observer loop. This is a usability correction, not a measured large-trace
throughput improvement or a new viewport-resize focus policy. No controls,
colors or persistent focus preferences are added. Regenerate older reports.

The browser regressions reopen at middle/bottom offsets by pointer and keyboard
in both themes and single/sidecar modes; preserve report geometry, data,
selection and time range; and cover text-only regions, native selects, disabled,
inert, hidden and nested-disclosure controls. Real keyboard input verifies both
native control editing and isolation from Timeline navigation. The existing
native/page fullscreen and idle-measurement regressions remain applicable.

The November 18 follow-up passes 395 distinct tests without skips: 56 overlay,
detail-action, fullscreen and search tests, plus 339 browser, mixed-sample,
detail-expansion, hover, position and saved-view tests. Complete sample JSON
payloads and Text chunks remain unchanged except generated build time; the
kernel/mixed reports retain 739/1,504 rows and 485/714 calls. Four regenerated
sample/detail screenshots are byte-identical. Logs and fidelity checks are
retained locally under `build/tools-focus-audit/`.

## Reproduction and tests

1. Generate or open a new mixed report and switch to Timeline.
2. Use a short landscape window, such as 568 × 320, and search for `.*`.
3. Open Tools. Check the first focused control and scroll to Scheduling latency.
   Both must be in the viewport and actually hit-testable, without page scrolling.
4. Repeat with Notes, desktop input, both themes and full screen. Resize or
   scroll the page while a panel is open, and check that Escape returns focus.

```bash
PYTHONPATH=tests python3 -m unittest test_toolbar_overlays -v
```

The suite checks placement arithmetic across viewport offsets and anchor
positions, actual Chromium geometry and hit testing, single/HTTP sidecar
delivery, both themes, short/roomy desktop and touch windows, menu scroll
preservation, content changes, missing viewport APIs, native/page full screen,
kernel/event-only reports without filtering and complete Timeline immutability.
Visible-viewport changes are additionally exercised with an explicit EventTarget
fixture; this is not a physical soft-keyboard test. Stable open/closed panels
are checked for unwanted geometry reads. Old HTML needs regeneration; captures
do not need to be repeated.

The initial related regression run passes all 126 cases without skips, including
the ten new model/browser tests and the existing detail, help, filter, hover,
saved-view and resize suites. Local before/fix observations and regression logs
are retained under `build/toolbar-overlays/`. These checks do not establish a
large-trace throughput or physical-device latency improvement.

The final broader regression run passes all 1,074 cases without skips in
884.427 seconds. It includes the new placement tests plus the existing browser,
search/keyboard, detail, filter, Text event/folding/navigation, source, BTF,
import and single/sidecar report suites. These overlapping test counts are not
additive. The result is retained in `build/toolbar-overlays/regressions.log`.

## Published examples and package checks

The refreshed examples reuse the saved captures and matching Linux `v7.4-rc2`
source/image at `7475ea8b58616b33c04afb976cf0fbaad9dd63fb`. Complete original
Text row contents and attributes, including event markers, source pools/maps,
Timeline identity and Timeline payloads agree with the previous reports;
only regenerated `stats.build_time` is excluded. Kernel and mixed examples
retain 739 / 1,504 Text rows, 485 / 714 recorded calls and 10 / 74 event rows.
No recapture, symbol simplification or evidence filtering is involved.

Actual Chromium layout observations after searching `.*`:

These observations compare the examples before the menu fix with revision
`348faa0a7d9f`. Later [search-toolbar sizing](search-toolbar.md) changes the
opener's position in affected windows, so exact current pixel positions may
differ; viewport containment and hit-testing remain the required invariants.

| Report | Window / input | Previous menu bottom | Updated menu bottom |
| --- | --- | ---: | ---: |
| Kernel | 568 × 320 / touch | 395.69 px | 145.8 px |
| Mixed | 568 × 320 / touch | 412.69 px | 145.8 px |
| Kernel | 640 × 480 / desktop | 502.5 px | 472 px |
| Mixed | 640 × 480 / desktop | 523.5 px | 472 px |

The short touch menu opens upward; the desktop menu is capped at the lower
viewport gutter. The last Scheduling latency action is fully inside and
hit-testable in all four corrected cases. Top/middle/bottom panel surface
checks also rule out occlusion by sticky tabs. Selection, view range, complete
Timeline JSON, canvas/detail/toolbar bounds and page scroll remain unchanged.
These are layout checks, not input latency or throughput benchmarks.

The portable `0.9.4` wheel and source distribution build successfully. The
isolated installed-wheel smoke run executes 14 cases: 10 pass, while four
optional native uftrace/raw/extra-dependency cases are skipped because those
payloads were not requested. A report generated by the installed CLI from
`tests/data/pi4_sched.txt` also passes the same short-touch/desktop placement,
hit-testing, state and complete-data checks. This is not validation of a native
recorder or raw-enabled wheel. No browser page errors occur in these probes.

All seven regenerated public/guide screenshots remain byte-identical; normal
appearance is unchanged, so no PNG assets need updating. Local before/after
screenshots, complete recorded-model comparisons, browser observations and
build/test logs are retained in `build/toolbar-overlays/`, including
`sample-fidelity.json`, `rendered-reports.json`, `mixed-before.png`,
`mixed-after.png`, `installed-smoke.log` and `screenshots.log`.

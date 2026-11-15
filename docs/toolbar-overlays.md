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

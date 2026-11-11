# Temporary expansion for cramped Timeline details

November 12, 2026. This follow-up addresses an observed short-landscape layout
problem without automatically hiding the chart or overwriting the user's split.
It applies to newly generated single-file and sidecar reports; existing HTML
must be regenerated to include it.

## Observed problem and behavior

In the saved QEMU scheduler-only projection at 844 × 390, the normal split leaves
only 67px for Tasks. Its touch-sized title occupies almost the whole pane. Rows
are reachable by scrolling, but the initial panel is not useful for reading.
Reducing chart height automatically would remove context during call navigation,
while changing the saved split would affect other reports and window sizes.

- Below 140px, a subdued expand icon appears beside the panel's Close button.
  Normally sized panels have no additional visible control. Touch targets are
  at least 44 × 44px; long call names reserve space for both icons.
- Expanding temporarily borrows the main chart area and its event-only banner.
  The toolbar and page header remain available. This differs from **Max / M**,
  which hides page chrome rather than allocating chart space to details.
- Pressing the same icon restores the split. Closing details also restores the
  chart. Keyboard Enter/Space activate the native button; Escape retains the
  existing behavior of dismissing help first, then closing details outside
  input controls. Help and its close button remain usable while expanded.
- The stored `fgTl.detailFrac`, time range, selection, query, task page, recorded
  identities and CSV scope do not change merely because the pane expands.
  Intentional navigation/filter actions retain their normal behavior.
- Expansion survives panel replacement, orientation changes and switching to
  Text and back within this Timeline instance. Closing the panel clears it;
  reloading the report starts with the saved normal split, not expansion.

For the observed scheduler projection, the explicit action expands the 67px
pane to 235px at 844 × 390, making task rows and paging controls visible together.
Restoring returns it to 67px without modifying the saved fraction. This is a
layout measurement, not a startup or rendering-speed benchmark.

## Implementation boundaries

The class is applied only to `#timelineView`, not an ancestor of Text's large DOM.
The panel uses the remaining flex space. The hidden main canvases retain their
model and previous dimensions; restoring performs a normal resize and redraw.
The chart container uses `display: contents` while expanded, keeping its existing
help node available relative to the Timeline root instead of cloning a dialog
or hiding it inside a `display: none` ancestor. Keyboard focus goes to a visible
panel control rather than an invisible canvas. Duration histograms in details
continue to resize independently of the hidden main canvases.

There is no new capture option, schema field, model truncation, source resolver,
native dependency, persisted expansion preference or changed evidence semantics.
Extreme viewport heights can still require scrolling; this does not guarantee
all controls, source blocks and rows fit simultaneously. Normal split behavior,
including its chart minimum and saved fractional sizing, remains unchanged.

## Reproduce

```bash
PYTHONPATH=tests:. python3 -m unittest test_detail_expand -v

python3 funcgraph.py samples/sample_mixed.jsonl --no-config \
    --timeline --no-text-view --output /tmp/mixed-details.html
```

Open a generated report in a short landscape window, select a call or open
**Tasks** from **More**, and press the expand icon beside Close. Check the same
icon, Close, help, paging and orientation changes. For a desktop reproduction,
drag the divider until the detail pane is shorter than 140px, then expand it.
The example command is for layout verification without source resolution; it
does not reproduce the source-annotated public mixed sample's generation flags.

## Validation

Eleven dedicated browser tests cover:

- Native keyboard expansion, exact restored height, unchanged manual fraction,
  selected call and time range; returning focus to a visible control.
- Light/dark landscape touch with device pixel ratio 2 across single-file and
  sidecar delivery, visible rows/pager, native paging and preserved task page.
- Header/keyboard help, Escape/Close, switching Text and back, reload semantics,
  and navigation among error, section, latency, exec/exit, task and call panels.
- Resizing, independent **Max** behavior, responsive duration histograms, long
  function names without overlapping touch controls, and byte-identical CSV.

The final focused regression runs 59 tests in 33.778 s, all passing, including
these eleven plus task paging, lifecycle/event-only reports, existing compact
layout/maximization, resizer behavior and view-specific header help.
The same eleven dedicated tests also pass in 7.414 s against an exported isolated
commit tree containing only this follow-up, without the pre-existing staged or
unstaged Text optimizations. This is a targeted check, not a second full audit.

Real-report checks use the saved 49-event QEMU scheduler projection (zero calls,
20 identities) and `samples/sample_mixed.jsonl` (714 calls, one recorded task
identity). Before/after Timeline models match the `a86780633cbc` baseline except
for measured `stats.build_time`. Twenty-four configurations cover both inputs ×
single-file/sidecar × light/dark × desktop/portrait/landscape. All preserve the
time range, selection, saved fraction and model counts when expanding/restoring;
all restore the original panel height and retain working native help, without
page errors or document-level horizontal overflow. Desktop/portrait checks
deliberately reduce the split first because normal-height panels do not show
the extra control. Screenshots were visually reviewed for task and mixed-call
details, including wrapped long names and touch control spacing.

These checks do not establish browser-engine equivalence or a general memory
bound. They exercise Chromium; no Safari/Firefox compatibility claim is made.

The final required-native/sample-browser audit runs 887 tests in 461.200 s:
883 pass, three skip and one fails. The only failure remains the documented,
unrelated and uncommitted Text intrinsic-size assertion,
`test_textchunks.TextChunkBrowserTest.test_native_layout_skips_offscreen_blocks_without_dropping_complete_records`:
the browser returns `auto` in computed intrinsic sizes while its assertion
expects bare lengths. Original staged and unstaged Text changes remain intact;
this follow-up does not modify that assertion or claim a green full suite.

```bash
FG_RAW_REQUIRED=1 FG_UFTRACE_REQUIRED=1 FG_SAMPLE_BROWSER_REQUIRED=1 tests/run.sh
```

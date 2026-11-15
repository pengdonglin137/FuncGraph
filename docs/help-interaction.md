# Help interaction and reading behavior

The header **?** remains the single help entry for the active Text, Timeline or
Compare view. Timeline exposes its existing toolbar fallback when maximization
hides the header. No additional toolbar control or preference is introduced.
Regenerate older reports to receive these changes.

## Reading and dismissal

Explicitly opened help no longer disappears after eight seconds. It stays open
until the user closes it, toggles the help entry, clicks outside, or changes views.
Opening help focuses its native close button without scrolling the page. Enter
or Space activates that button; Escape or `?` also closes help when focus is inside.
The panel is non-modal: Tab can move to other page controls normally.

Navigation keys used inside help do not move the Text selection or pan, select,
recolor or toggle inline frames in the chart behind it. Unmodified arrow/page keys
and Home/End scroll help itself, not the page behind it, even when help is short.
Mouse/touch scrolling remains available. The close button stays at the top of the panel while
its contents scroll, and the Text/Compare panel has an opaque theme background
so trace text does not show through its instructions.

Closing from inside help returns focus to its opener when that element still
exists and can receive focus, without jumping the page. If the opener was removed
or hidden, an available help entry is used instead. Clicking outside or changing
views closes help without pulling focus away from the user's new destination.
Inputs, composition, consumed events and browser modifier combinations retain
their existing native handling. Escape in maximized Timeline closes help first;
it does not also exit maximization or clear the selected call.

The header and maximized fallback expose the controlled panel and open/closed
state through `aria-controls` and `aria-expanded`. The existing panel names and
native close buttons remain; no modal focus trap is added.

## Deferred preparation

Timeline help still waits for its existing view preparation rather than forcing
synchronous initialization. A second entry activation, Escape, Tab or a click
elsewhere cancels a pending help request, not the Timeline preparation itself.
An old completion cannot reopen canceled help or take focus back. Switching views
also cancels the pending request and closes the previous panel without restoring
focus into the view being hidden.

Deferred Text initialization can prepare its normal logical selection while help
is open, but does not focus the new row over a focused help panel. This does not
change trace data, source expansion state, selection rules outside help, or the
default renderer. It also does not add a per-row help index or scan the full Text
DOM for help controls: the two entry buttons use direct ID lookup.

## Evidence and verification

A real generated sample reproduced both original issues: opening help left focus
on the header entry, Tab moved to the information button rather than into help,
and the panel disappeared after 8.2 seconds. The retained local observation is
`build/help-lifecycle/baseline.json`.

`tests/test_help.py` checks persistence beyond the old timer, native activation
and focus return, unchanged underlying navigation/state, opaque panel styling,
scrolled close-button hit targets, portrait/landscape touch layouts, removed
openers, maximized Timeline, plain Text, Timeline-only reports, sidecar delivery
and delayed Text completion. Existing browser tests additionally exercise staged
Timeline cancellation, repeated requests, Tab/outside-click focus retention and
view changes. Light and dark themes are covered.

```bash
PYTHONPATH=tests python3 -m unittest test_help -q
PYTHONPATH=tests python3 -m unittest \
  test_browser.BrowserSmokeTest.test_pending_timeline_help_can_be_cancelled_without_cancelling_the_view \
  test_browser.BrowserSmokeTest.test_pending_help_does_not_reopen_after_tabbing_or_clicking_elsewhere -q
```

These are interaction/layout checks, not new startup or event-toggle speedup
measurements. Trace rows, source mappings and Timeline payloads are independently
checked when the published samples are regenerated.

The final browser/Text/source/Timeline/report/sidecar regression run passes
**734 tests**, and the matching-vmlinux sample/BTF/name/inline/usage run passes
**60 tests**. A final rerun of help, event visibility and pending-help controls
passes **26 tests** against the final assets.

The complete real report additionally checks **372,673 Text rows**, including
**88,199 event rows**, without constructing the complete Text model for help.
In a controlled reference differing only by removal of help-local scroll-key
routing, PageDown in short help scrolls the underlying page by **787px**. The
final runtime keeps that scroll at **0px**, preserving selection, folding and
focus return. This is a behavior comparison, not an old-production startup or
speed benchmark. Both variants retain the identical 346,786,834-byte Text
subtree (SHA-256
`88d5eb1c95d8d6cd1c80fcaf9fd6b45dcd18ee3a2525714ac5706f68c84d10cf`)
and unchanged source arrays, source maps and Timeline payloads. Full observations
are retained locally in `build/help-lifecycle/real-report.json`.

Regenerated published samples preserve all **739 kernel rows / 10 events** and
**1,504 mixed rows / 74 events**, original source mappings and Timeline data
except the regenerated `stats.build_time` timestamp.

Wheel and source-distribution builds succeed. Installed-wheel smoke checks run
**14 tests**, passing with **4 optional native/extras checks skipped**. Default
sample images and the selected-call detail image remain byte-identical; only the
help screenshot changes to reflect the reachable close control.

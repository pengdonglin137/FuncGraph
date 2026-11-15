# Field-filter popup interaction

Text's existing field buttons keep their compact layout and native checkboxes,
inputs and action buttons. This change fixes focus and dismissal rather than
adding another toolbar control, a modal dialog or a new filter language.
Regenerate older reports to receive the updated runtime.

## Behavior

- Enter, Space or clicking a field opens a named non-modal popup and focuses its
  first input without scrolling the reading area. Checkbox Space and ordinary
  Tab retain their native behavior. The opener exposes its expanded state and
  controlled popup through ARIA; free-form inputs have an accessible field name.
- Add/OK keeps the existing append-and-apply behavior. Cancel and Escape discard
  only unapplied choices. Closing from inside returns focus to a still-visible
  enabled opener. Repeated activation toggles the same popup closed; choosing
  another field replaces it.
- Outside clicks and closing after focus has already moved elsewhere do not take
  focus back. Escape closes a child field popup before the compact Filter panel,
  without also invoking trace navigation or closing source blocks.
- Hiding Filter, switching views, changing compact layout or clearing the filter
  removes the body-mounted popup and resets its opener state. No delayed input
  focus remains after dismissal or view switching.
- Composition/legacy-IME events, already-consumed keys and Ctrl/Meta/Alt key
  combinations do not trigger popup Enter/Escape actions. No focus trap is added.
- Compact touch layouts use 44px checkbox-label rows and action buttons, plus
  a 44px/16px-font free-form input. Desktop dimensions stay unchanged. Cancel
  uses the theme's ordinary foreground instead of low-contrast secondary text.

Only applying a condition changes the filter. Canceling preserves trace rows,
selection, folding, open source panels, event visibility and Timeline evidence.
Opening and canceling a popup does not build the complete Text model. Existing
filter/model/layout costs when applying conditions are not removed or bounded
by this interaction change.

The single open popup and its owner are retained directly. Cleanup and Escape
do not search the complete trace DOM for popup/active-field selectors, and the
no-popup path returns immediately. No per-row handlers or indexes are added.

## Evidence and tests

The previous real `sample.html` reproduced four issues: enum popup focus stayed
on its field button, Tab moved to the next field instead of popup choices,
desktop Escape left the enum popup open, and free-form Escape dropped focus to
the document body. An explicit Text-to-Timeline switch also left the body-mounted
popup visible. Local observations are in
`build/text-field-controls/baseline.json`.

The original compact popup had **24.5px checkbox-label rows** and **17px action
buttons**, measured on the real sample at 390×844. Final measurements show
**44px rows/buttons** and **16px free-form input text**, with no page scroll on
opening. Desktop row/action dimensions remain 24.5px/17px. Touch screenshots
and measurements are retained under `build/text-field-controls/`; Chromium
touch emulation is not a physical-device or on-screen-keyboard test.

`tests/test_filter_controls.py` covers native activation, focus/ARIA, confirm,
cancel, replacement, repeated entry, two-step Escape, outside focus, view/layout
changes, ignored keyboard events and unchanged trace/source/event state. It
checks desktop, light/dark touch portrait/landscape, plain Text, standalone,
sidecar and experimental windowed Text. These are browser interaction and
layout checks, not startup or event-toggle performance measurements.

```bash
PYTHONPATH=tests python3 -m unittest test_filter_controls -q
```

The browser/Text/source/Timeline/report/sidecar/prefilter regression run passes
**758 tests**. The final **13 field-popup tests** pass. A final combined popup/event/help and
compact-navigation run passes **39 tests**, and the matching-vmlinux
sample/BTF/name/inline/usage run passes **60 tests**. Wheel/sdist builds succeed;
installed-wheel smoke runs **14 tests**, passing with **4 optional native/extras
checks skipped**.

The complete retained real report checks **372,673 Text rows / 88,199 events**.
Popup entry, focus return and Text-to-Timeline dismissal leave selection,
folding, source expansion and event visibility unchanged; the complete Text
model stays unprepared. All source payloads and Timeline data remain identical,
including after Timeline initializes. The original 346,786,834-byte Text
subtree remains byte-identical (SHA-256
`88d5eb1c95d8d6cd1c80fcaf9fd6b45dcd18ee3a2525714ac5706f68c84d10cf`).
Full observations are in `build/text-field-controls/real-report.json`.

Refreshed published samples preserve **739 kernel rows / 10 events** and
**1,504 mixed rows / 74 events**, original source mappings and Timeline payloads
except regenerated `stats.build_time`. Default sample, Text, Timeline detail and
help screenshots remain byte-identical; popup changes do not affect the closed
toolbar layout.

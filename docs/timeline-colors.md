# Timeline duration colors

**Timeline → Tools → Color** offers **Total time** and **Self time**, alongside
the unchanged default **Context** mode. This is display-only: no trace fields,
source links, arguments, call relationships or saved addresses are rewritten.

## Meaning and scope

- Total time uses each real call's duration, including children and recorded waits.
- Self time subtracts direct recorded child-call durations and clamps to zero.
  It is not exclusive on-CPU time: untraced work and waits can remain.
- Each mode uses 20 blue-to-red `log1p(duration_ns)` bins, normalized against
  its own report-wide maximum, not the current view, task, search or filter.
- Zero maps to blue; the maximum positive duration maps to red. All-zero or
  empty inputs never divide by zero or produce invalid colors.
- Overhead subtraction recomputes both maxima and updates the active scale.
  The menu explicitly labels estimates. Recorded positions and widths never
  change, so adjusted duration colors can differ from measured slice width.
- Duration-based packed reports retain the modes. Call-order reports without
  durations and event-only reports hide them rather than color synthetic units
  as elapsed time. Packed coordinates are still not wall-clock timestamps.
- Synthetic inline and gap frames retain their neutral styling. Error overlays
  still override fills; disable Errors when comparing only duration colors.
- Mixed address-space boundaries remain. The User/Kernel color swatches only
  appear in Context mode, where their colors have the advertised meaning.

## Layout and accessibility

The scale and a short explanation live in the existing Tools menu, not a new
toolbar row or a floating canvas overlay. Closing Tools restores the full chart
area. The same controls work in normal/full-screen layouts and both themes;
the native labeled dropdown exposes its selected option without wrapping a row
of mode buttons. The scale's
numeric range and textual meaning remain available without interpreting color.
The gradient uses the same palette function as the slices and follows theme
changes, including while the menu is closed.

## Cost and verification

Maxima and logarithmic denominators are computed alongside the existing self
time calculation, not by rescanning calls on each draw. A slice needs one
`log1p` and a bounded cache lookup; the two heat modes use at most 40 cached
fills in total. Legend generation visits the 20 palette bins only when colors,
theme or overhead change. There are no new network resources or trace columns.

`tests/test_timeline_colors.py` covers the live model/rendering helpers and real
Chromium controls: inclusive/exclusive semantics, legacy self palette, clamping,
zero/empty/one-nanosecond inputs, bounded caching, overhead, stable global scale,
error precedence, neutral synthetic frames, themes, packed/count/event-only
reports, single HTML and HTTP sidecar delivery, mixed-space swatches, keyboard
operation, compact/full-screen bounds and unchanged recorded data/geometry.

Run it from the repository root:

```bash
PYTHONPATH=.:tests python3 -m unittest test_timeline_colors -v
```

The public kernel and mixed sample HTML reports include the controls; their
default Context screenshots intentionally do not change to a duration mode.

# Reusing native Text widths when toggling events

This optimizes **Events: shown / hidden** in large, fully mounted single-file
reports. It does not change recorded rows, folding/filter rules, source contents,
Timeline data, capture options or the default renderer. Regenerate old reports
to receive the runtime change.

## Problem and implementation

The preceding [staged-startup change](performance-text-startup.md) makes the
complete 372,673-row report open successfully, but its event toggles still take
roughly 10–15 seconds. A separate profile of that same complete report attributes
8.4 seconds of a 9.5-second hide and 15.5 seconds of a 15.9-second show to
`fgMeasureTextLayout`: native max-content chunk widths are remeasured even though
only event visibility changes.

During ordinary native chunk measurement, event-containing chunks now briefly
give each trace row its own max-content width. The browser measures the chunk
and its rows, then the runtime remembers two exact widths per eligible chunk:
the current maximum including eligible events, and the maximum excluding events.
These are actual native measurements, not character-count estimates. The
temporary measurement styles are removed before returning; normal row styling
and horizontal scrolling do not change.

An event-only projection can reuse the appropriate width. Height counts and
skipped-layout state still refresh, and every event row remains in the DOM. The
cache is a WeakMap keyed by chunk, not a second per-row data model. Toggling
events still does not prepare the complete analysis/folding model.

The cache deliberately fails closed:

- Regular filter, fold, ordering and geometry refreshes invalidate affected chunks.
- Viewport width, device pixel ratio, theme, compact layout, root styles and Text
  size/padding form a context key; changed contexts cannot reuse old widths.
- If events are hidden during measurement, their unobserved shown width stays
  unknown and showing them requires a fresh native measurement.
- Unexpected chunk children, inconsistent event visibility or a row maximum
  differing from the measured chunk width prevent caching.
- Expanded source siblings remain excluded from intrinsic trace-row measurement,
  as before. Their nodes, contents and expansion state are not rebuilt by toggles.
- Hidden Text keeps its existing pending-measurement path and refreshes on return.

Small reports and deferred/windowed sidecar Text retain their existing paths.
There is no new toolbar control, dependency, format version or user option.

## Complete real-report comparison

The retained **2026-11-15 Host QEMU/KVM** capture has **372,673 original Text
rows**, **88,199 event/helper rows** and **142,240 function calls**. Its raw
SHA-256 is
`68a371a3688dd67f06a3f24f198269058372b591f7c4a809cfbe74d7bde6bd50`.
The forced single-file report is about 344 MiB, without resolved source bodies.
The Host source is not inferred from the unrelated Guest image.

Before/after entries differ only in the two frontend assets. The entire Text
subtree is byte-identical: **346,786,834 bytes**, SHA-256
`88d5eb1c95d8d6cd1c80fcaf9fd6b45dcd18ee3a2525714ac5706f68c84d10cf`.
No recapture, sampling, filtering, windowing or replacement with a smaller
fixture is used for this comparison.
Source arrays, source-line mappings, Timeline identity and the entire Timeline
payload are also byte-identical, including build metadata in this controlled
asset-only comparison (`build/text-event-widths/fidelity.json`).

Three pairs of independent Chromium **145.0.7632.6** processes alternate old/new
order, over the actual loopback-only `fgview` server. No tests or builds run
concurrently. Each run begins with an unprepared Text model, then hides, shows,
hides and shows events. Median milliseconds:

| Toggle | API before → after | Two-rAF feedback before → after |
| --- | ---: | ---: |
| First hide | 9,749.0 → 531.6 | 9,855.9 → 678.1 |
| First show | 12,960.6 → 1,122.7 | 12,983.6 → 1,150.9 |
| Repeat hide | 11,947.6 → 490.4 | 12,058.9 → 535.2 |
| Repeat show | 15,667.5 → 486.4 | 15,690.0 → 508.5 |

The additional initial native row reads have a cost. Navigation-to-load samples
are **22,215.2 / 22,386.4 / 22,369.3 ms** before and
**22,970.0 / 22,962.0 / 23,157.3 ms** after. The median increases from
**22,369.3 to 22,970.0 ms**, about **601 ms / 2.7%**, in exchange for removing
repeated full-width measurements on these event toggles. This is not a startup
speedup or a cold operating-system-cache guarantee.

Every run retains and validates every original record and visible line after
each toggle. All complete-record and per-toggle visibility signatures agree;
the complete-record signature is
`f3cc1ff42230ffba9ce719e8f5d884ab0f51b3929cd8dbb0beb5d658ca00bc4d`.
No toggle prepares the complete model. All individual timings, report hashes,
navigation observations and signatures are retained locally in
`build/text-event-widths/paired.json`; the independent earlier profile is in
`build/text-event-widths/profile.log`.

## Verification and limits

Browser regressions compare the cached size of every fixture chunk with a fresh
native max-content measurement, without the temporary per-row measurement style,
within 1/64 CSS pixel. They also compare complete vertical/horizontal geometry
with fully visible native layout. Desktop light and narrow dark touch contexts
include long Unicode/tab event payloads, very long function arguments and open
source panels. Repeated event toggles perform no new native chunk measurements
in the unchanged context, keep source nodes intact and leave the model lazy.
Additional checks cover filter/fold/order changes, hidden Text resizing, changed
font context and unknown-child fallback.

An independent check also measures **all 728 chunks of the complete real report**
with events hidden and shown. Every reused width exactly matches a fresh native
max-content chunk measurement without the temporary per-row style: maximum
difference **0 CSS pixels** in both states. Root height, root width and horizontal
extent also equal fully visible native layout. Both toggles perform zero new
native chunk width reads, retain every original row/event, leave no temporary
measurement styles and keep the model unprepared. Results and the local verifier
are `build/text-event-widths/geometry.json` and `verify_geometry.py` in that directory.

These reference checks use fresh independent pages and synchronous geometry reads,
not extra timing samples. An earlier combined diagnostic completed the hidden
reference but was stopped during the subsequent forced-visible rendering round
trip; that artificial full-paint path is retained separately in
`build/text-event-widths/geometry-forced-roundtrip.log`, not counted as a completed
shown reference or a production toggle measurement.

The related Text/model/source/Timeline/report/sidecar regression run passes
**471 tests**. Matching-vmlinux sample/BTF/name/inline/usage checks pass **60
tests**. The wheel and source distribution build successfully; installed-wheel
smoke reports 14 tests with four optional native/extras cases skipped. Refreshed
samples retain every original row, source mapping and Timeline payload except
the regenerated build time; all three tracked screenshots remain byte-identical.
The earlier unrelated broad-browser failures remain documented in the
[startup verification notes](performance-text-startup.md); these selected-suite
results are not a claim that every repository test passes.

These timings are not instant interaction: new-runtime samples still reach
**1,156 ms** of synchronous API work, and two-rAF feedback is a proxy, not
physical presentation or worst-case input latency. Row projection and layout
reset work remain; invalidated or unknown widths still require native layout.
This does not bound full-DOM/model memory or make initial layout cooperative.
Earlier prepared sidecar [projection measurements](performance-text-events.md)
describe a different geometry path and must not substitute for this comparison.

```bash
python3 tests/benchmark_text_events.py BEFORE.html AFTER.html --cold-model \
  --rounds 3 --output event-widths.json
PYTHONPATH=tests python3 -m unittest test_text_events test_text_layout -q
```

Normal users should regenerate their report, not manually replace embedded
assets. Asset replacement here is only the controlled old/new experiment setup.

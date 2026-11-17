# Full-screen Timeline in very short viewports

## Reproduced issue

The chart deliberately retains a 160 px minimum height and the detail panel
retains 60 px. With toolbar rows and padding, these can exceed a short window.
At 320 × 240 desktop-pointer dimensions, the captured kernel sample's selected
call details began at y=328; the expand button extended to y=395. Both browser
full screen and the page-viewport fallback clipped this content because their
fixed root used `overflow: hidden`. Native wheel/touch scrolling could not
reach it. Programmatic scrolling could still move that hidden container, so
an automation-only `scrollIntoView()` check was insufficient to detect the bug.

The ordinary, non-full-screen report was also checked: it can scroll the page
to reach its minimum-height split. That behavior is not a clipping failure and
is unchanged by this fix.

## Behavior

The existing full-screen root now permits vertical native scrolling when its
content does not fit. Horizontal overflow remains hidden, and overscroll is
contained within the full-screen report. The chart and detail minimums stay
160/60 px; no calls, tracks, flags, source information or controls are removed
to make the layout fit. Normal-sized full-screen windows do not gain a
scrollbar when their content already fits. Existing detail expansion can still
borrow the chart area, restore the split and close normally after scrolling.

A passive root-scroll listener hides stale hover content, closes Actions if
its anchor moved, and schedules the existing Tools/Notes placement routine.
Without it, permitting root scrolling could leave fixed overlays attached to
an old screen position. Hidden menus retain their existing fast return; the
listener does not rebuild the Timeline, scan trace records or request data.
Track scrolling, detail-body scrolling, model selection, zoom, filters and
the saved split retain their existing semantics. Exiting full screen restores
ordinary page behavior; native browser full screen and the fallback share
the same policy.

No button, setting, automatic panel expansion or new visual language is added.
Old standalone reports embed their previous runtime and must be regenerated
to receive the fix.

## Validation

- Native wheel input, not programmatic scrolling, first proves that the short
  full-screen root moves. The unchanged baseline fails this assertion in both
  single-file and sidecar delivery without harness errors.
- Kernel and mixed inputs cover both delivery modes, native/fallback full
  screen, desktop/coarse-pointer layouts and light/dark themes: 32 wheel
  configurations. The original model, input JSON, view, selected call and
  saved split remain unchanged during scrolling and detail expansion/restoration.
  Expanding, closing and exiting full screen remain usable; the interaction
  makes no additional requests.
- Bounded CDP touch sequences exercise actual native touch scrolling in both
  full-screen modes, including repeated edge swipes and return to the top.
  These are Chromium-emulated touch checks, not universal physical-device or
  mobile-browser guarantees.
- Tab can reach the detail-expansion control, and Enter/Space activate the
  native button without changing the model or saved split. A normal-sized
  full-screen positive control stays non-scrollable when everything fits.
- Root-scroll regressions check hover invalidation, moved Actions dismissal
  and focus restoration, and Tools geometry/hit testing after its anchor moves.

Final validation on November 18, 2026 passed 360 distinct tests with no skips:
218 full-screen, resize, help, overlays, keyboard, positioning, focus, colors,
hover, tooltip and sidecar regressions, plus 142 parser, Timeline, uftrace,
required-native-raw, report, source-index and documentation checks. Native raw
coverage ran with `FG_RAW_REQUIRED=1`; real-input report checks also included
the local `write4`, host-QEMU and kernel-default captures.

Both public reports were regenerated. Their five embedded JSON payloads
(`fgSrc`, `fgSrcLines`, `fgErrnoNames`, `fgTimelineIdentity`, `tlData`) remain
identical except for `tlData.stats.build_time`: all 485 kernel-sample and 714
mixed-sample Timeline calls remain present. Full HTML matches the baseline
after substituting only the changed CSS/panel runtime and normalizing the
generation timestamp and processing timings. Five regenerated public/guide
screenshots are byte-identical to their baselines.

An additional public-sample visual control at 1400 × 860 produces byte-identical
before/after full-screen screenshots in both native and fallback modes. At
320 × 240, native wheel input leaves the baseline at scrollTop=0 but moves the
fixed report to scrollTop=156 in both modes, bringing its detail controls into
view while preserving the 160 px chart. These checks establish reachability
and layout preservation, not a rendering-speed improvement.

Run the focused coverage with:

```bash
PYTHONPATH=tests python3 -m unittest test_timeline_fullscreen test_timeline_resize \
  test_toolbar_overlays test_help test_timeline_keyboard test_position \
  test_timeline_focus test_timeline_hover test_tooltip -v
```

Before/after observations, expected-failure proof, generated-report fidelity
and final validation logs are retained in `build/fullscreen-scroll-audit/` in
the development checkout. The earlier broad panel audit is retained under
`build/position-scroll-audit/`; off-screen controls in ordinary page mode are
not counted as failures when normal page scrolling can reach them.

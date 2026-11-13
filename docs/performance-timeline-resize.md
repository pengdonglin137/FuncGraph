# Timeline resize integrity and latency flags

## Scope and root cause

The November 13, 2026 fix is compared with `a58b600ea10a`. It fixes the
detail-panel drag path, not the entire cost of opening a large report.

Previously `resize()` assigned both canvas backing-store dimensions even when
they had not changed. Assigning either dimension clears the canvas immediately.
The drag handler called `resize()` in one animation callback, but `draw()` queued
painting for the next callback. Interleaving repeated pointer moves with painting
therefore exposed a cleared chart and ruler. Merely drawing less often would not
fix this ordering error.

`resize()` now updates CSS geometry and requests painting. Backing-store sizes
are synchronized only when different, immediately before the full chart/ruler
paint in the same callback. Initial font measurement still runs if the initial
canvas dimensions already match. The scene cache, selection, time range, device
pixel ratio and fractional-pixel handling are retained. Ending, cancelling or
losing pointer capture removes all four drag listeners and cancels the pending
resize callback. Previously the unused once-only cancellation handler survived
each normal drag completion.

## Flags are retained, not reconstructed

The reported kernel-only `write4.html` already contained latency flags on all
29,586 calls. The default Context colors, irqs-off hatching, need-resched marks
and optional Preempt depth overlay still use the same flags. Their explicit
text was inside the collapsed **Trace metadata** section. It now appears in the
main call facts without expanding that section, retaining the original preempt
count and trace-baseline explanation.

`none` is shown only for recorded, clear latency flags. Calls without latency
data do not get a flags row; missing data is not converted to zero. No parser,
BTF interpretation, context inference, baseline, statistics, recorded address,
source position, argument or return value is changed by this fix.

## Real-report comparison

The original 65,648,506-byte report was left untouched. A sibling
`/vol_1t/Qemu/x86_64/share/write4.fixed.html` substitutes only the embedded
`assets/timeline/render.js` and `assets/timeline/panels.js`. Reversing those exact
substitutions reproduces the original file byte for byte, including every Text
row, source block and JSON payload. Original SHA-256:

```text
6802111a869b7d85b701504dd40644badc477d0c14eb6d9b90a90faf675de1d3
```

Three before/after pairs ran serially in independent Chromium 145.0.7632.6
processes at 1500 × 900, alternating the order on the second pair. Each run
selected the first call, made five four-step drags and requested twelve
same-size resizes. A deliberately interleaved animation callback sampled both
canvas alphas after the drag's resize callback and before the deferred painter.
The complete serialized `fgData.read('tlData')`, selection and time range were
checked before and after interaction and across reports.

| Observation | Before | After |
| --- | ---: | ---: |
| Clear chart/ruler samples during 60 sampled drag frames | 60 | 0 |
| Backing-store assignments for twelve same-size resizes, per run | 24 width + 24 height | 0 |
| Active cancellation listeners after five drags, separate once-aware pair | 5 | 0 |
| Flags text visible without expanding metadata | no | yes |
| Recorded calls / calls with latency data | 29,586 / 29,586 | unchanged |
| Text rows / DOM elements after selecting the call | 38,938 / 596,830 | unchanged |
| Calls with irqs off / need-resched | 2,430 / 30 | unchanged |
| Complete Timeline JSON identity, selection and time range | baseline | identical |
| JavaScript errors in all three runs | 0 | 0 |

The trace also retains its 58 softirq and 189 hardirq calls, one BH-off call,
raw preempt counts and baseline of one. Existing timestamp-clamping diagnostics
are not changed or claimed to be fixed here. Dense overview pixels still need
zooming to distinguish individual slices; no recorded calls are removed.

For transparency, median file-open time was 7,797.3 ms before and 7,692.6 ms
after; median first Timeline readiness was 475.6 ms and 463.0 ms respectively.
Three local pairs do **not** establish a general startup-performance improvement.
The large single-file Text/source DOM remains expensive to construct. This fix
does not hide that cost by truncating calls, stripping sources/arguments,
changing default rendering modes or disabling features.

Raw evidence is in `build/validation/20261113-timeline-resize/`:
`reproduction.json`, `write4-flags.json`, `write4-comparison.json`, the before/after
screenshots and `sample-runtime-identity.json`. The first comparison's listener
instrumentation counts registrations without explicit removal, including
callbacks automatically removed by native `once`. It is not an active-listener
count. `write4-listener-cleanup.json` uses wrappers that account for native
`once` removal, and the regression test covers normal completion, cancellation
and real pointer-capture loss.

## Regression and use

```sh
PYTHONPATH=.:tests python3 -m unittest test_timeline_resize -v
PYTHONPATH=.:tests python3 -m unittest \
  test_browser.BrowserSmokeTest.test_timeline_flags_are_visible_without_opening_trace_metadata \
  test_browser.BrowserSmokeTest.test_timeline_zero_latency_flags_are_explicit_not_missing \
  test_browser.BrowserSmokeTest.test_timeline_user_calls_do_not_invent_unrecorded_kernel_flags \
  test_browser.BrowserSmokeTest.test_timeline_calls_without_latency_data_do_not_show_false_flags
```

Resize coverage includes single/sidecar reports, light/dark themes, DPR
1/1.25/2, rapid drag frames, repeated same-size resizes, repeated normal/cancelled
drags, lost capture, pending-callback cancellation, double-click reset and
fullscreen round trips. Adjacent browser tests compare scene-cache pixels with
full redraws at fractional/high DPR. Data equality checks retain the entire
Timeline model, not just counts.

The initial 527-test adjacent/browser run exposed only the old layout test's
two single/sidecar subtest expectations that flags must be hidden. Those
expectations were updated to verify visible flags through metadata toggles,
unchanged recorded data and readable raw-only arguments. All 284 adjacent tests
then passed, including the new initial-font-metrics test; the 244 browser tests
had passed in the initial run against the same production code. A final
11-test resize/flags/cursor suite also passed. Both samples, the guide screenshot,
assembled JavaScript syntax and 206 local Markdown links were verified.

`sample.html` and `sample_mixed.html` receive the same runtime-only substitutions,
with their embedded trace/source data unchanged. Existing standalone HTML
contains its own runtime: installing updated FuncGraph does not rewrite old
reports. Regenerate with the original source/trace options to receive UI fixes;
there is no need to omit Text, source information or arguments. Sidecar remains
an optional full-data delivery mode, not a prerequisite for this repair.

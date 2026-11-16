# Function-graph preempt baselines

## Reading the warning

`baseline` is an inferred correction for the function tracer's own recording path,
not the selected function's preempt count. An entry count above an entry baseline
of zero can indicate disabled preemption. Conversely, a tracer-added level must
not turn a normal `schedule()` entry into an atomic-sleep warning. The warning is
FuncGraph's interpretation of captured flags and its sleep-capable function list,
not evidence that the kernel emitted a BUG message.

## Reproduced failure

The preserved Linux 6.8 recording
`build/captures/20261115-host-qemu-functions/host-functions.txt` contains:

```text
5600306.469910 |   13) qemu-sy-1675103 |  ...1. |               |      schedule() {
5600306.476696 |   13) qemu-sy-1675103 |  ..... | # 6786.143 us |      } /* schedule = 0x0 */
```

It also contains zero-depth task-context trace events. Previously, scanning all
latency columns together let either events or returns lower the entry baseline
to zero. All 124 recorded `schedule()` entries have raw preempt depth 1; all 124
were incorrectly flagged. Entry and return paths need not have the same offset.

## Corrected interpretation

| Record | Calibration population | Model field |
|---|---|---|
| Function entry / folded leaf | Task-context entries / leaves only | `preempt_base` |
| Function return | Task-context `}` records only | `preempt_exit_base` |
| Event / comment / IRQ marker / header | Excluded from both populations | No function-tracer correction |

Each minimum is capped at one. A minimum of two or more therefore still leaves
visible preempt-off depth. With no entry evidence, the entry baseline is zero;
with no return evidence, the return baseline defaults to the entry baseline.
uftrace uses the corresponding kernel `entry` / `exit` records, excluding user
records. Raw trace-cmd inputs follow the same decoded graph-line rules as text.

- Sleep warnings use the **entry** count and baseline. `schedule()` remains in
  the sleep-capable list, preserving warnings for genuine invalid callers.
- Sections and function-record resched maxima use the current record kind's
  baseline. Events do not open/close sections; their resched counts are not
  reduced by a function-tracer baseline.
- Normal slices use entry flags. Orphan slices have only return evidence, so
  their detail badges, tooltips and preempt overlay use the return baseline.
  Legacy models without the new field fall back to their existing entry baseline.
- Text rows, recorded flags, call relationships, durations and source data are
  not rewritten. Text's raw `preempt:>1` query is an entry-oriented convenience,
  not a universal baseline-aware query across different record types.

These are data-driven inferences, not independently measured tracing overhead.
In particular, a lowest entry count of one cannot by itself distinguish a
tracer-added level from a real task level present throughout the capture.
Zero corrected depth is therefore not proof that sleeping is safe; IRQ/BH and
interrupt-context checks remain independent.
Regenerate an older HTML report to apply corrected calibration; a browser reload
alone cannot repair a model embedded by an earlier generator.

## Regression checks

The same 142,240-call recording now yields entry baseline 1, return baseline 0,
and no warnings for those 124 normal `schedule()` calls. Local before/after
summaries and test logs are retained under `build/preempt-baseline/`.

Tests cover event/header/IRQ-marker exclusion, independent zero/one entry and
return offsets, folded leaves, all-preempt-off captures, IRQ/BH and genuine
invalid `schedule()` callers, return-only observations, resched maxima, uftrace
mixed inputs, raw 32/64-bit little/big-endian containers, and browser detail and
legacy-fallback behavior. Recorded flags remain unchanged.

Validation on November 16, 2026:

- 138 model/import/raw/report regressions passed, including the real recording
  supplied through `FG_TRACES`; none were skipped.
- 67 selected browser, hover, duration-color and zero-value regressions passed.
- An isolated installation of the portable wheel passed 14 smoke tests,
  including the new baseline case, native raw decoding and optional ELF/source
  support. The bundled-uftrace-wheel case was skipped: this check did not build
  or validate a native uftrace payload.
- 19 archived Python, asset, test, manifest and documentation files matched the
  checkout byte for byte across the wheel and source distribution. The new
  explanation is explicitly included in `MANIFEST.in`.
- Both public reports were regenerated with the same source revisions. All
  original Text rows/attributes, source payloads and call columns matched their
  previous versions; only preempt-derived data and viewer explanations changed.
  The mixed screenshot's Notes count increases because its baseline explanation
  is now available. The errors screenshot also refreshes an older CPU-header
  label to the current recorded-scheduling wording; it is not a trace-data change.

```bash
PYTHONPATH=tests python3 -m unittest test_timeline test_uftrace_import -v
FG_RAW_REQUIRED=1 PYTHONPATH=tests python3 -m unittest test_raw -v
PYTHONPATH=tests python3 -m unittest \
  test_browser.BrowserSmokeTest.test_timeline_schedule_ignores_event_and_return_offsets_without_hiding_atomic_calls \
  test_browser.BrowserSmokeTest.test_timeline_orphan_preempt_flags_use_return_baseline_with_legacy_fallback -v
```

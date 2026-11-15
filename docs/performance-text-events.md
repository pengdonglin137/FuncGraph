# Text event visibility: local updates and native keyboard controls

## Problem and change

The **Events: shown / hidden** control changes event visibility, not function
visibility. Previously, full mounted Text nevertheless projected every authored
record through the fold renderer. It repeated function DOM lookups, visibility
checks and open-source fill/cancel calls even when those functions were unchanged.

The prepared Text model now retains a list of references to its event records.
Full Text projects only those records using the existing fold/filter predicate,
then refreshes affected layout blocks and keyboard visibility. The original
complete record array and record identities remain intact. Wire index versions,
row columns, line numbers, filters, Timeline evidence and source state are unchanged.
Legacy indexes without event metadata retain an empty event list.

Single-file Text can initially render without a complete analysis/folding model.
The event button now uses its existing event DOM rows in that case, rather than
constructing the complete model on first click. Both paths share the same
fold/filter predicate; prepared folding models retain their ancestry cache.
Explicit navigation can also find a hidden event in the existing DOM and restore
it without preparing the complete model. A saved hidden preference follows the
same path on reload.

Event DOM writes and affected layout work remain. Sidecar data loading is not
changed. Windowed Text still rebuilds its complete visibility sequence and mounts
its bounded window; this change is not a new windowing algorithm or memory limit.

The same review found that Text's global keyboard handler intercepted Space on
toolbar buttons and could use Enter to expand the selected row instead of
activating the focused control. It now respects already-handled events, native
buttons/links/forms, disclosure summaries, button-role controls and editable
content. Reading-area shortcuts remain available outside those controls.

## Verification

Browser regression tests first reproduced both all-record projection and failed
Space activation on the previous runtime. They now assert:

- Only event rows reach the full-Text projection path during either toggle.
- Open source DOM identities, expansion state and fill/cancel work stay untouched.
- Enter/Space activate the focused event button; arrows do not move Text selection.
- Links, summaries, button-role controls and editable content keep their keys;
  ordinary reading-area `j` and Home navigation still work.
- Function/loss records, complete Timeline data, filters, folding, persistence,
  explicit event navigation and single/sidecar/windowed behavior are preserved.
- Optional event metadata in index versions 1–5 retains exact record references;
  missing metadata and malformed references keep their existing behavior.
- A first toggle, explicit hidden-event navigation and restoring a saved choice
  leave initially unprepared single-file Text models unprepared.
- The shared visibility predicate agrees for DOM rows and prepared records,
  including nested folds, filtered subsets and large original line numbers.
- At a 320px mobile viewport with coarse-pointer/touch behavior, active filtering
  and either theme, visible primary controls stay in view with 44px touch targets.

The runtime and UI assertions are in `tests/test_text_events.py` and
`tests/test_textmodel.py`. Timing is not a pass/fail unit-test threshold.

## Performance method

`tests/benchmark_text_events.py` compares full mounted reports over the real
loopback-only `fgview` server. By default, Text and its complete folding model are
ready before timing. `--cold-model` instead requires initially unprepared,
unfolded and unfiltered full Text; it records whether each toggle prepares the
model and validates original DOM records without preparing it during validation.
This means an old runtime's first-toggle model preparation is included in that
first API interval. Each fresh Chromium run measures hide/show/hide/show,
preserving all records. It checks every visible original line, hashes the complete
authored records and each visibility result, and rejects differences between
before/after reports. No trace is recaptured or pre-filtered for comparison.
Source nodes are covered separately by browser regressions; the real Host report
has no resolved source panels.

The API duration includes synchronous projection and its Promise completion.
Two `requestAnimationFrame` callbacks provide a separate feedback proxy, not
physical presentation, frame rate or worst-case input latency. Initial download,
full mount and validation hashes are outside those intervals. Model preparation
is excluded in default mode but included if a cold-mode toggle causes it.
Browser/server caches are not forced cold. No tests or builds run concurrently
with the paired timing experiment.

## Prepared-model real-report results

These measurements describe the earlier event-only projection optimization
(`4e99a6bd0fe7`), not the subsequent removal of first-toggle model preparation.

The 2026-11-15 Host QEMU capture contains **372,673 Text rows**, of which **88,199**
are tracepoint or task-switch hint rows. It retains 142,240 function calls.
Using Chromium **145.0.7632.6**, three pairs of independent browser runs alternate
before/after order. Only the two changed frontend assets differ between entries;
both reference the same immutable sidecar resources. Median milliseconds:

| Toggle | API before → after | Two-rAF feedback before → after |
| --- | ---: | ---: |
| First hide | 846.2 → 414.2 | 889.5 → 457.8 |
| First show | 859.7 → 476.4 | 880.4 → 501.0 |
| Repeat hide | 761.9 → 392.7 | 800.0 → 432.1 |
| Repeat show | 705.3 → 379.8 | 724.8 → 401.1 |

API work decreases by approximately **45–51%** for these operations. It still
takes roughly 0.4–0.5 seconds in this large full-DOM report: this is not a claim
of instant interaction or a solved large-Text startup problem. The small
event-reference array adds retained references, not a second copy of row data.

All six runs have identical complete-record and visibility signatures; the
complete Text-record signature is
`9a5a32af1b5c9c67aafc0550e7c220a162fa79215a27c3bafa68f0ab0edf86ed`.
The local complete results are in `build/events-visibility-perf/paired-final.json`.
First-hide API samples are 812.1 / 933.0 / 846.2 ms before and
414.2 / 404.7 / 430.6 ms after. All samples, feedback times, report SHA-256s,
record counts and visibility hashes remain in the JSON, not only the best run.

## First-toggle measurement limit

A separate `--cold-model` attempt used the same complete 372,673-row capture
rendered as a forced single-file report (360,581,275 bytes, about 344 MiB).
Chromium navigation exceeded the benchmark's 240-second `load` timeout before
any toggle was measured. This is a report-startup failure, not a measured slow
toggle or a successful first-use comparison. Its local diagnostic is retained
in `build/events-lazy-model/initial.log`; no full-report cold-model speedup is
claimed. The lazy-model regressions verify the removed model construction, not
that large single-file startup or layout costs have disappeared.

The cold-mode harness was also exercised on the separate published mixed sample
(1,504 rows, 74 events), using three alternating before/after browser pairs.
Every old-runtime toggle reported a prepared model; every new-runtime toggle
left it unprepared. Complete row and visibility signatures matched across all
six runs. A prepared-model pair passed as well. These are harness/behavior smoke
checks on a smaller input, not substitutes for the failed full-capture run;
the local results are `build/events-lazy-model/sample-cold.json` and
`build/events-lazy-model/sample-prepared.json`.

## Reproduce

For matching old/new reports, retaining identical data and replacing only the
changed frontend assets:

```bash
python3 tests/benchmark_text_events.py \
  /path/to/report/events-before.html /path/to/report/events-after.html \
  --rounds 3 --output /path/to/event-toggles.json
python3 tests/benchmark_text_events.py \
  /path/to/single-before.html /path/to/single-after.html \
  --cold-model --rounds 3 --output /path/to/first-event-toggles.json
PYTHONPATH=tests python3 -m unittest test_text_events test_textmodel -q
```

For sidecar input, the selected entry files must remain in the dedicated report
directory beside its `.funcgraph-sidecar` marker and immutable data directories.
Use regenerated reports for normal operation; replacing embedded assets is only
the controlled benchmark setup.

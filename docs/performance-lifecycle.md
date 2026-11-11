# Lifecycle panel and marker regression benchmark

November 11, 2026. This change targets Timeline's **Exec / exit** panel and
the corresponding track-header markers, not the complete large-trace pipeline.
The usage and evidence semantics are documented in the
[English](uftrace.md#generate-a-report) and [Chinese](uftrace.cn.md#生成报告) guides.

## What changes

- The event panel replaces its table body when paging. At most 100 event rows
  are attached, including after visiting later pages; every event remains
  available to CSV export and Text navigation.
- Trace-time buttons pan to the recorded event at the current zoom. They do
  not select an invented call, change shared filters, unhide filtered tracks
  or join unverified clocks. They also work without Text.
- Per-task marker indexes use binary search to skip offscreen timestamps.
  Dense ranges draw each occupied CSS pixel once per task, skipping other
  events at that pixel by binary search. This preserves the original opaque
  marker geometry; table and export records are not coalesced.
- Only the separate marker index is sorted when needed. Report event order,
  duplicates, trace lines and original timestamps are unchanged.
- Touch controls have 44-pixel minimum heights. Timing explanations use a
  native disclosure instead of filling the first mobile viewport. Short
  panels scroll their title normally so it cannot cover pagination controls.

This is a panel-DOM and redraw optimization, not bounded report memory:
the full event list and indexes still reside in memory. CSV export still
traverses the complete list and constructs the download in memory.

## Reproduce

Generate single-file Timeline reports using the before and after code with
the same options, preferably `--timeline --no-text-view`. Then run:

```bash
python3 tests/benchmark_lifecycle.py before.html after.html \
    --events 100000 --frames 20 --rounds 3 --output lifecycle-benchmark.json

PYTHONPATH=tests python3 -m unittest test_lifecycle -v
```

The benchmark substitutes the same synthetic one-task, one-call report and
100,000 alternating exec/exit events into both HTML templates. These are
scaling fixtures, not claims about a real task's lifecycle. Each round opens
a fresh page; report order alternates between rounds. It measures 20 redraw
callbacks per time window after three warmups, then one panel click handler.
The JSON retains the browser version, every timing sample and attached DOM
counts. Input, browser, viewport and round count should be held constant.

## Local result

Host: x86_64 Intel Core i7-13700; headless Chromium 145.0.7632.6;
1500 × 900 CSS-pixel viewport at device scale 1. The baseline uses the
Timeline assets at `a5ca3c6155c5`; unrelated uncommitted Text changes were
identical in both templates. Three alternating rounds produced:

| Measurement | Before | After |
| --- | ---: | ---: |
| Event rows attached to the panel | 100,000 | 100 |
| Descendant element nodes in the panel | 600,012 | 721 |
| Panel click-handler median | 2,248.4 ms | 5.8 ms |
| Narrow-window redraw-callback median | 0.55 ms | 0.20 ms |
| All-events overview redraw-callback median | 21.20 ms | 0.80 ms |

Panel timing includes synchronous DOM/style/layout work performed by the
handler, not the complete next paint or time to interaction. Redraw timing
covers the scheduled canvas callback, not compositor presentation. These
numbers do not measure startup, JSON decoding, source loading, CSV export,
recording overhead or whole-report memory, and are not universal guarantees.

The browser regression suite additionally compares occupied marker pixels
against a full-scan reference, counts timestamp reads and verifies that a
100,000-event panel still attaches only 100 rows. It covers unordered and
duplicate markers, exact viewport boundaries, per-task pixel independence,
event-only tracks, empty/exact/partial pages, keyboard activation, sidecar
Text loading, timeline-only positioning, clock separation, filter refresh,
64-bit timestamp export, CSV formula escaping, both themes, touch portrait
and landscape, and manually shortened panels. Timing thresholds are not
used as correctness assertions.

## Verification on the working tree

All 14 lifecycle browser tests pass. The complete suite was also run with
native uftrace and the mixed-sample browser checks required:

```bash
FG_UFTRACE_REQUIRED=1 FG_SAMPLE_BROWSER_REQUIRED=1 tests/run.sh
```

Result: **846 tests, 842 passed, 3 skipped, 1 failed**, in 430.221 seconds.
The sole failure is the existing uncommitted Text optimization's
`test_native_layout_skips_offscreen_blocks_without_dropping_complete_records`:
Chromium serializes intrinsic sizes as `auto 13056px` / `auto 2728.5px`, while
the assertion expects the lengths without `auto`. The preceding full-suite
log contains the same failure. Those staged and unstaged Text patches were
verified byte-for-byte unchanged before and after the lifecycle commit.
This work does not alter that assertion or claim the full suite is green.

## Event-only follow-up (November 11, 2026)

The CLI previously discarded every zero-call Timeline, including valid scheduler
events and uftrace exec/exit milestones. With `--no-text-view`, that also caused
an error claiming that Timeline was missing. Reports now retain recognized timed
observations without inventing function calls. Runs-only and wakeup-only tasks
are visible in this mode; normal call reports keep their existing track policy.
A muted notice explains the zero-call view. Call-only hiding is disabled, and a
shared Text filter leaves scheduler/lifecycle context unfiltered. Missing BTF,
argument and call-site warnings are not emitted for a function-free trace.

Kernel events also establish kernel address-space presence when no kernel
functions were recorded. User calls and kernel scheduler events with unverified
clocks therefore retain separate task identities, even when their PIDs match.
Empty, metadata-only, loss-only, unknown-event-only and untimed inputs still do
not produce an event-only Timeline. A single milestone keeps a zero-length
observed extent; display zoom is not a measured lifetime or CPU duration.

Validation commands:

```bash
PYTHONPATH=tests:. python3 -m unittest test_event_only -v
PYTHONPATH=tests:. python3 -m unittest \
    test_event_only test_lifecycle test_timeline test_timeline_zero test_uftrace_import -v
FG_UFTRACE_REQUIRED=1 FG_SAMPLE_BROWSER_REQUIRED=1 tests/run.sh
```

- The 19 dedicated tests pass, including seven browser tests. They cover raw
  timestamps above JavaScript's exact integer range, zero-span milestones,
  scheduler-only text, wakeup-only identities, clock separation, all report
  delivery modes, `--no-text-view`, operation without vmlinux/source, CSV,
  Text/shared-filter behavior, and touch portrait/landscape in both themes.
- The focused regression runs 74 tests: 73 pass and one external-trace test is
  skipped. The complete suite runs 865 tests in 431.882 seconds: 861 pass,
  three are skipped, and the same pre-existing Text intrinsic-size assertion
  described above fails. The unrelated staged and unstaged Text patches remain
  byte-for-byte unchanged.
- A scheduler-only projection of the saved QEMU Linux `7.4.0-rc2` recording keeps
  all 49 original kernel events: 14 switches, 28 waking events and seven wakeups.
  With matching `mono` clock metadata, it produces zero calls, 20 task identities,
  11 off-CPU intervals and 28 nonduplicated wake observations, without vmlinux or
  source arguments. Both single-file and sidecar output succeed.
- Eight real-data browser configurations cover both output modes, desktop light
  theme, phone portrait in both themes and dark touch landscape. All 20 task
  tracks remain available, without page errors or horizontal page overflow.
  These are projections of saved events, not new captures or reconstructed
  missing function calls. No new performance or total-memory claim is made.

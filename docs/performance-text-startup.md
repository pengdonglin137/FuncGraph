# Staging large single-file Text startup

This changes initial rendering, not capture, decoding, row contents or the
default Text renderer. Regenerate older HTML to receive it.

## Problem and implementation

A large single-file report previously exposed its complete Text subtree while
the HTML parser was still appending rows. Native offscreen chunk layout was
enabled only by `fgInitText` at window load. Chromium could repeatedly style,
lay out, paint and layer the growing uncontained subtree before that point.
The eventual geometry preparation then had to work with that expensive state.

Reports with at least **4,096 original Text rows** now carry an inline-startup
hint. For initial Text in a browser supporting `content-visibility: hidden`,
the early head script temporarily suppresses the Text subtree's rendering while
the browser parses it. A subdued **Preparing trace text…** status remains visible;
the Trace text region is marked busy. This is not a filter or windowed renderer:
every original row is still parsed and retained in the DOM.

`fgInitText` first enables the existing supported chunk geometry mode, then
removes the temporary suppression and status before measuring. Removing the
status before navigation avoids changing row geometry after an anchor or
keyboard target has been positioned. Initialization clears the busy marker and
temporary helper. A load-event timer also clears staging if initialization
returns early or throws, so this protection does not strand readable Text behind
a hidden style.

Small reports keep their existing progressive rendering. Saved initial Timeline
or Compare uses its existing hiding path; deferred sidecar Text does not activate
inline staging. Without JavaScript, or without support for the temporary CSS
property, no staging style is installed and the original Text remains readable.
No new permanent toolbar control, dependency, index version or user option is
introduced.

## Full-capture evidence

The retained 2026-11-15 Host QEMU/KVM capture contains **372,673 original rows**,
**88,199 event/helper rows** and **142,240 function calls**. The raw input's
SHA-256 is
`68a371a3688dd67f06a3f24f198269058372b591f7c4a809cfbe74d7bde6bd50`.
It is the complete capture, not a reduced, sampled or pre-filtered substitute.
The single-file report is about 344 MiB; no vmlinux/source bodies are supplied.
The Host kernel's source is not inferred from the unrelated Guest image.

The previous ordinary cold-model benchmark timed out at 240 seconds during
navigation. A separate instrumented diagnostic against the pre-staging runtime
(`5b83c441deab`) reached DOMContentLoaded at about 179 seconds and started its
load handler at about 185 seconds; initialization was still unfinished at the
360-second timeout. An initial trace buffer filled during the repeated partial
paints, so it is not presented as a complete startup profile or a timing baseline.

A controlled staging prototype opened that same complete report in about
22 seconds with diagnostic instrumentation. That result motivated the production
change; it is not a paired production speedup measurement. The original timeout
logs and diagnostic trace remain under `build/single-startup/` and
`build/events-lazy-model/initial.log`.

The complete Text HTML subtree is byte-identical before and after staging:
346,786,834 bytes, SHA-256
`88d5eb1c95d8d6cd1c80fcaf9fd6b45dcd18ee3a2525714ac5706f68c84d10cf`.
Source arrays, source-line mappings, Timeline identity and all Timeline data
also match, excluding only the generated `stats.build_time` value. The local
comparison is retained as `build/single-startup/fidelity.json`.

## Production-run observations

Three independent Chromium **145.0.7632.6** processes opened the production
report over the real loopback-only `fgview` server, with no concurrent tests or
builds. All use the same complete input above, not the smaller regression fixture.
Observed navigation-to-load times are **21,833.2 / 22,577.6 / 22,482.3 ms**
(median **22,482.3 ms**). DOMContentLoaded begins at 4,161.2 / 4,768.4 / 4,367.4 ms.
These are completed new-runtime observations against prior timeouts, not a paired
old/new percentage speedup or a cold operating-system-cache guarantee.

Each run then hides/shows/hides/shows events, verifies every original Text record
and visible line, and confirms that no complete analysis/folding model is prepared
by these actions. All 372,673 rows and all 88,199 event/helper records are retained;
complete-record and visibility signatures agree across runs. The complete-record
signature is
`f3cc1ff42230ffba9ce719e8f5d884ab0f51b3929cd8dbb0beb5d658ca00bc4d`.
Full results, asset/report hashes, navigation timing and individual toggle samples
are in `build/single-startup/cold-after.json`.

This full single-file workload also reveals a remaining problem: median API
durations for the four event toggles are **9,802.9 / 12,913.9 / 12,506.2 / 15,344.5 ms**.
Updating full Text chunk geometry is still expensive. These are not the earlier
prepared sidecar event-projection measurements, and they must not be substituted
for one another. Staging fixes repeated startup rendering; it does not establish
fast event toggles in a large measured-geometry single-file view.

## Verification and remaining limits

Browser regressions pause parsing after the first 512 rows and assert that the
loading message is visible, Text rendering is suppressed and the region is busy.
After releasing parsing, all 4,203 fixture rows, long arguments and final rows
remain, temporary state is gone, and complete geometry matches a forced fully
visible layout. The paused checks include desktop light and narrow dark modes,
with and without Timeline.

Additional cases cover the threshold boundary, saved Timeline startup, deferred
sidecar delivery, no JavaScript, missing CSS support, early-return and thrown
initializer failures, source expansion, folding, filtering, anchors, native find,
selection and view round trips. They are in `tests/test_text_layout.py` alongside
the existing geometry invariants.

The related Text/model/source/Timeline/report/sidecar suite passes **468 tests**;
the matching-vmlinux sample/BTF/name/inline/usage suite passes **60 tests**.
The wheel and source distribution build, and installed-wheel smoke reports
14 tests with four optional native/extras scenarios skipped. Regenerated sample
rows, source payloads and Timeline payloads retain their pre-change contents;
the tracked screenshots remain byte-identical.

A broader 377-test browser run has two failing assertions: pressing `0` on a
focused fold button is expected to expand descendants, and the Tree guides
button is expected to have an x-coordinate below 150px. Both reproduce with
the exact pre-change frontend assets, on a fixture without the startup hint.
They are not silently counted as passing or changed as part of this startup fix.
The baseline reproducer log is `build/single-startup/baseline-failures.log`.

This does **not** bound DOM/model memory, stream or window single-file Text,
remove geometry work, or make the final initialization cooperative. Width
measurement and complete navigation preparation can still block the main thread
for a large report. The loading status is not a frame-rate or time-to-interactive
guarantee. Existing sidecar and experimental windowed Text choices remain useful.

```bash
python3 funcgraph.py TRACE --no-config --no-btf --timeline --enable-fold \
  --filter --report-mode single --output REPORT.html
python3 tests/benchmark_text_events.py REPORT.html --cold-model --rounds 3 \
  --output event-startup.json
PYTHONPATH=tests python3 -m unittest test_text_layout test_report test_sidecar -q
```

The event benchmark records navigation/load observations separately from its
event-toggle intervals. It validates every original row and every visible line;
timings are not pass/fail test thresholds. Browser navigation timings and two-rAF
feedback are not physical presentation measurements. A timeout before toggling
must not be reported as a successful event-toggle measurement.

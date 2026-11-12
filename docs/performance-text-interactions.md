# Shared Text interaction handlers

November 12, 2026. This reduces per-row event-handler overhead while retaining
the complete Text DOM, source behavior and original records. Regenerate older
reports to benefit; both single-file and sidecar reports use the same runtime.

## Evidence and implementation

A CPU/DOM inspection of the 72,176-row `/tmp/pi4/traceB.txt` sidecar found
approximately 1.94 million DOM nodes and 72,300 registered event listeners.
Every line number carried its own inline anchor-copy handler; source-expandable
rows additionally carried click and double-click handlers. No source bodies
were available in this large fixture, so its excess listeners came from line
numbers rather than source links.

New markup omits those three per-row handlers. `fgInitText` installs one click
and one double-click delegate on the existing Text root, once after mounting:

- A line-number click copies that row's anchor without expanding source.
  Nested targets inside the number use the same route.
- Source-row clicks retain the existing delayed toggle, selection protection,
  keyboard-selected identity and expand/minus button behavior. Double-clicks
  retain native selection and cancel a pending source toggle.
- Native source links keep their own outgoing-link behavior. Fold controls
  continue using their existing delegated handler, without toggling source.
  Expanded source panels are siblings, not trace rows, and are not routed as
  row clicks.
- Reordering moves existing rows without rebinding them; view roundtrips do
  not install additional listeners. Keyboard handling is unchanged.
- If older inline attributes are present, the delegate leaves that action to
  them instead of invoking it twice. No per-row handler migration scan runs
  after mounting.

This bounds these three interaction bindings, not every listener in a report:
outgoing source links and other controls can still have their own handlers.
Rows, raw text, source metadata, anchors, fold relationships and analysis
models are not removed or reconstructed from a visible subset. It is not DOM
windowing, bounded memory, a new capture format or a change to `fgcapture`.

## Large-trace comparison

Baseline runtime: `9e4480633214`. Input: 72,176 original rows, 55,186 calls,
55,213 frames and 12 tracks, without vmlinux/source bodies. Both sides use
`--no-config --no-btf --timeline --enable-fold --filter --report-mode sidecar`.
Three alternating pairs run in independent Chromium 145.0.7632.6 processes,
1500 × 900, through real `fgview` HTTP. No competing tests or builds run during
these measurements. Values are medians, not performance guarantees.

| Observation | Before | After |
| --- | ---: | ---: |
| Initial Timeline ready, ms | 186.1 | 184.1 |
| Complete Text ready, ms | 3177.6 | 3024.6 |
| Parsed-status feedback, ms | 76.0 | 70.5 |
| Longest Text long task, ms | 192 | 175 |
| Sampled Text renderer RSS, MiB | 801.7 | 785.4 |
| CDP registered event listeners | 72,300 | 126 |
| Mounted Text rows / model records | 72,176 / 72,176 | 72,176 / 72,176 |
| Paint blocks | 141 | 141 |
| Text network resources | 18 | 18 |

The listener difference is exactly the 72,176 removed line-number handlers
minus the two added delegates. All six complete Text model digests agree:
`bfcd594748ec635ac430b942433cff198a922f635af4ddf97573afe30fc1f9c6`.
Browser errors/CSP violations are zero. The CDP counters cover the whole page
and are point-in-time observations, not a complete retained-object proof.
DOM nodes remain approximately 1.94 million. RSS is sampled, not peak memory
or a bound; two-rAF/status feedback is not an input-to-pixel measurement.

The approximately 153 ms Text-ready improvement and 16.3 MiB sampled RSS
difference apply to this fixture and these runs. They do not prove a universal
speedup, source-rich performance, or completed large-trace virtualization.
HTML parsing, full model preparation and final synchronous layout still need
further work. Source-capable browser fixtures separately verify interaction
semantics, not source-rich performance.

## Reproduce

Generate reports from the same input and arguments using the baseline and
updated runtimes, then compare independent alternating browser runs:

```bash
python3 funcgraph.py /path/to/traceB.txt --no-config --no-btf \
    --timeline --enable-fold --filter --report-mode sidecar --output REPORT_DIR
PYTHONPATH=tests python3 tests/benchmark_text_delivery.py BEFORE_DIR AFTER_DIR \
    --rounds 3 --output interactions-benchmark.json
PYTHONPATH=tests python3 -m unittest -v test_textviewport test_textchunks
```

`first_text.dom_counters` now includes CDP document/node/listener snapshots
alongside the existing timing, full-model digest and resource checks. The
interaction regressions cover single/sidecar, 4,204 source-capable rows,
exactly one delegate per event after reordering/view switches, native links,
one anchor copy, source buttons, fold controls, double-click selection,
expanded-source descendants, older inline markup, keyboard Enter and
separate real touch taps in light/dark themes. Rapid double-taps remain
native double-click/selection gestures rather than two independent toggles.

## Validation

The complete native/raw/browser suite runs 1,147 tests in 752.754 seconds:
1,144 pass and three optional external-data checks skip. Both public HTML
reports are regenerated; their screenshots remain byte-identical. Seven
kernel golden reports retain all normalized content except the intended
handler removals/runtime update. Wheel and sdist pass Twine and fresh-venv
smoke checks; all 17 packaged frontend assets match the working tree.

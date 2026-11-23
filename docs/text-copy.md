# Reliable Text copying

## Reproduced behavior

Chromium checks on the complete native sample reproduced three independent
problems in the Text toolbar's existing Copy button:

- The compatibility path reported `Copied 739 lines` even when
  `document.execCommand('copy')` returned `false`.
- Repeated copies saved temporary success messages as the original label, leaving
  the button stuck on `Copied 739 lines` after all reset timers completed.
- Selecting the temporary textarea cleared the reader's selected trace text and
  moved focus away from the button.

The original probe and its results are retained locally at
`build/text-copy/before.json`.

## Behavior and boundaries

The existing button, placement and copied-row rules remain unchanged. Copy reads
non-hidden trace rows in their current order, including offscreen records. It
respects filters, call folding and event visibility, excludes line numbers and
expanded source, and uses the complete visible-record sequence for windowed
Text without mounting the whole report. Its tooltip and both usage guides now
make those rules explicit; no extra toolbar control is introduced.

Each button retains its original label/background and a generation for its
current request. Copy shows `Copying…` while a modern clipboard request is
pending, then either the copied line count or `Copy failed`. Empty results do not
write to the clipboard. Feedback restores the original label/background after
one second, including after repeated clicks. Stale completions cannot overwrite
newer feedback or initiate a compatibility copy, even after a prior feedback
timer has expired. The browser's already-submitted Clipboard API requests cannot
be cancelled; this does not claim control over the OS's completion ordering.

Missing or rejected modern clipboard support falls back to the browser's legacy
copy command. Success requires its actual Boolean success result; exceptions and
`false` are failures. The temporary textarea is always removed. Focus, forward/
backward/collapsed document selection, input selection and reading position are
preserved. This only handles copying: it does not modify capture data, record
indexes, source mappings or Timeline data, and does not add dependencies.

## Copying large windows

Windowed Text now reads dense Copy results directly from the committed
visibility mask and the time, task or duration order. It keeps one byte per
indexed row for the dense projection. Sparse results still walk only the
visible sequence. Neither path needs to mount rows, fetch sources or wait
for an in-progress filter or sort. The compatibility path's temporary
textarea no longer dismisses the compact More panel or loses Copy focus.

On `ping3` in Chromium 145 at 4× CPU throttling, five alternating fresh-page
pairs with seven measured copies per page gave median times to submit the
clipboard payload of **33.9 → 23.1 ms** in time order and **32.6 → 25.1 ms**
in task order. Each output matched its baseline byte for byte. These times
measure collection and serialization up to a stubbed clipboard call, not
the operating system's clipboard write. Results:
`build/benchmarks/20261122-interaction-hotspot/copy-ab.log`.

## Regression coverage

`tests/test_text_copy.py` includes thirteen browser tests covering native and
mixed recordings; plain, standalone, sidecar and truly windowed Text; event
hiding, filtering and folding; both themes and mobile failure feedback; original
label/background restoration; stale successes/failures and expired generations;
source-node identity and open-source state; native input selection; no-match
results; and no Timeline/source requests from copying.

Both the modern Clipboard API and legacy `execCommand` path are also exercised
against Chromium's actual clipboard and read back for exact equality. Failure,
permission denial and completion ordering are deterministic controlled stubs,
not claims to have reproduced every browser/OS permission configuration.

```bash
PYTHONPATH=tests python3 -m unittest test_text_copy test_text_events \
  test_filter_controls test_field_filters test_text_errno_labels \
  test_uftrace_text_returns -q
```

## Complete-report evidence

A Chromium probe uses the complete local `write4.html` capture, replacing only
its frontend script and adding the current errno dictionary. This is a frontend
rebase, not CLI regeneration or new capture/type evidence. The original
65,751,022-byte file remains unchanged; all retained prefix/suffix bytes,
38,938 rows and return metadata are identical.

Copy matches all 38,938 projected trace lines exactly (6,349,694 UTF-8 bytes),
without reading any report-data payload or preparing the lazy full Text model.
A failed legacy copy reports failure, retains selection/focus/scroll and restores
the Copy label. No page errors occur. This is a correctness check, not a
performance benchmark or a claim of asynchronous large-result serialization.
The probe and complete results are retained at
`build/text-copy/verify_real.py` and `real-report.json`.

## Validation artifacts

- The focused Copy/events/filter/errno/uftrace-return run passes **72 tests**,
  including all **13 new Copy tests**.
- The wider browser/Text/source/Timeline/report/sidecar/uftrace/BTF run passes
  **888 tests**, with matching `FG_VMLINUX` set and no skips.
- Matching-vmlinux sample/usage/BTF/clone/name/inline checks pass **77 tests**,
  with no skips.
- Wheel and sdist builds succeed. Installed-wheel smoke runs **14 tests**, with
  **4 optional native/extras checks skipped**. A browser also opens an installed
  CLI-generated report: hiding its 10 event rows copies exactly 729 trace rows,
  retains all 739 original row IDs, accurately reports failure and restores the
  original label without page errors.
- Both regenerated samples preserve all 739 native rows / 10 events and 1,504
  mixed rows / 74 events, all row attributes/text and source pools/maps, and all
  Timeline identity/data except regenerated `stats.build_time`. Sources remain
  pinned to Linux v7.4-rc2 at `7475ea8b58616b33c04afb976cf0fbaad9dd63fb`.
- Full-sample browser probes retain event restoration, exact return filtering
  and Text/Timeline round trips with no Timeline changes or page errors.
- Default native/mixed, expanded-Text, folding, Timeline-detail and help
  screenshots are regenerated and remain byte-identical.

The logs, sample-fidelity results, rendered-sample probes and installed-browser
results are retained under `build/text-copy/`.

# Text ordering from complete records, not live rows

November 12, 2026. This moves time/task ordering, task headers and switch-gap
descriptions into the complete Text record model. It is a prerequisite for
rendering unmounted rows, not a claim that DOM windowing is implemented.
Regenerate existing reports; no new option, CSS or data schema is required.

## Model and projection

`fgTextModel.prepareOrder(model, mode)` prepares a DOM-free plan containing
the complete numeric row permutation, positioned header/gap descriptions,
group-hidden separator identities and switch-marker identities.
`fgTextModel.order()` provides the existing synchronous interaction path.
Completed time/task plans are cached by model identity; cancelled preparation
does not publish a partial plan. Preparation yields while scanning, summarizing
and ordering rows. A saved task order in sidecar is prepared before any Text
rows are mounted, within the existing cancellable staged loader.

The plan preserves the original behavior:

- Header/comment rows before the first task remain first. Untasked rows follow
  the preceding task; only untasked separator/blank rows inside a group are
  hidden. All original rows remain in the permutation.
- Tasks with more member rows come first; ties retain first-observed order.
  Idle identities remain per CPU and sort after non-idle tasks. Without PID,
  per-CPU groups still distinguish CPU 0 from a missing CPU.
- Headers, their full-trace line counts and CPU lists are independent of the
  current filter/fold state. Gap descriptions retain the original switch,
  resumed row, available timestamp difference and intervening real-row count.
- The raw record order, filter facts, source IDs, argument/return values and
  kernel/user address spaces are not mutated by presentation changes.

The current renderer projects the plan onto the existing row/source pairs in
bounded-size paint chunks. It validates complete membership before replacing
the content and updates navigation with the same numeric permutation. Header
text is assigned with `textContent`, including unusual task names. Cold
standalone ordering can prepare its model while the root is detached; it does
not depend on finding that detached root through `document.getElementById`.
Duration sorting keeps its existing separate presentation path.

## Independent real-trace comparison

Baseline runtime: `07aa64307092`, including punctuation compaction, Worker
checksums and one-chunk lookahead. Both reports use the same 72,176-row traceB
input, SHA-256
`5954b07778c00623e0bb7324cd5e78130bb5188463ab7a3ca061eb89b2f1ab62`,
with `--no-config --no-btf --timeline --enable-fold --filter --report-mode sidecar`.

Three alternating pairs each use fresh Chromium 145.0.7632.6 processes and
1500 × 900 contexts, through actual `fgview` HTTP. Each opens complete Text
from Timeline, then switches task/time four times. No tests or builds run
concurrently with timing. First-task/first-time rows use three observations
per version; repeated-task/repeated-time rows use nine each. Values are
medians; feedback means two rAF callbacks, not physical input-to-pixel latency.

| Operation / observation | Before | After |
| --- | ---: | ---: |
| First task order, synchronous ms | 527.6 | 357.9 |
| First task order, feedback ms | 587.7 | 414.5 |
| Repeated task order, synchronous ms | 495.9 | 301.7 |
| Repeated task order, feedback ms | 552.2 | 358.0 |
| First restore to time order, synchronous / feedback ms | 364.8 / 373.9 | 310.4 / 320.0 |
| Repeated restore to time order, synchronous / feedback ms | 354.8 / 363.2 | 302.8 / 317.4 |
| Repeated task order, longest long task ms | 496 | 301 |
| Repeated task order, summed blocking above 50 ms | 451 | 257 |
| Sampled renderer RSS after eight reorders, MiB | 1236.0 | 1128.2 |

Repeated task-order feedback is about 35% lower in this comparison. RSS is
sampled after the eight actions, before untimed fidelity hashing; it is not
peak memory, a limit or proof of bounded retention. Full Text ready also
measures 2446.1 / 2368.4 ms, but this is context, not a separate causal startup
speed claim. Results do not establish behavior on other machines or browsers.

All six complete raw model hashes match:
`bfcd594748ec635ac430b942433cff198a922f635af4ddf97573afe30fc1f9c6`.
Untimed complete-state hashes also agree across all runs, including every row's
identity, grouping/switch flags and visibility, each header/gap position and
text, paint-chunk row counts, and the navigation permutation. Task order has
all 72,176 rows, 12 group headers and 28 gap descriptions; time order retains
the same rows without headers/gaps. No browser error or CSP violation occurs.

## Fidelity and regression scope

Pure-model tests cover sparse identities, exact gaps, stable ties, idle/per-CPU
groups, missing/empty attributes, empty/header-only input, old index versions,
cache identity and cancellation in every preparation phase. Thirty seeded
random cases compare with an independent Python full-record reference.
Browser checks reject live row-text/task-attribute reads during cached
projection, verify saved-order preparation before mount, and fail closed on
incomplete mounted membership without discarding the remaining rows.
Existing source pairing, folding, filters, duration-sort restoration and
navigation checks remain applicable.

An additional source-rich mixed-report comparison covers single-file task/time
order, user-only and kernel/IRQ filtering, and clearing the filter in eight
real-browser contexts (light/dark × 390px touch/1500px desktop × before/after).
All 40 complete projected states agree, including every row's markup, address
space, classes/visibility, source-pair reference, headers, filter hits and
navigation. Seven real-kernel golden reports also retain their complete
normalized output after the exact JS asset updates. The 18 real-trace Text
chunks and full filter index remain byte-identical; both manifests and all
20 resources per report pass length/SHA-256 checks, and complete Timeline
data agree except diagnostic build time.

Both public reports are rebuilt using their original source settings and the
matching kernel commit `7475ea8b58616b33c04afb976cf0fbaad9dd63fb`. Their complete
normalized output is identical after only the exact three changed JS assets
are replaced in the baseline. All three public PNGs remain byte-identical.

The full suite runs 1,225 tests in 750.643 seconds: 1,222 pass and three
optional environment-gated checks skip, with raw, uftrace and sample-browser
checks required. Matching-vmlinux BTF and extra-real-trace checks pass
separately; the optional kernel-tool comparison remains skipped, not verified.
Existing unclosed-file/socket ResourceWarnings remain in the log.

Pure wheel, source archive and bundled-uftrace x86_64 wheel each install with
extras outside the checkout. Each runs 14 smoke checks with raw enabled:
pure/source correctly skip one bundled-recorder check, while native passes
all 14 with uftrace required. Twine accepts all three; changed source,
assets, tests and documentation are checked byte-for-byte inside the final
source archive. These are local validation artifacts, not a new release,
version tag or Pages deployment.

## Reproduce and remaining work

```bash
python3 funcgraph.py /path/to/traceB.txt --no-config --no-btf \
    --timeline --enable-fold --filter --report-mode sidecar --output REPORT_DIR
PYTHONPATH=tests python3 tests/benchmark_text_order.py BEFORE_DIR AFTER_DIR \
    --rounds 3 --output text-order-benchmark.json
PYTHONPATH=tests python3 -m unittest -v test_textmodel \
    test_browser.BrowserSmokeTest.test_text_order_by_task \
    test_browser.BrowserSmokeTest.test_text_order_keeps_chunks_bounded_and_source_pairs_intact \
    test_sidecar.SidecarBrowserTest.test_saved_task_order_is_prepared_before_any_text_rows_are_mounted
```

Reports, original input, benchmark JSON and verification logs are retained
locally under `build/benchmarks/20261112-text-order/`; large evidence is not
committed. The cache adds complete numeric plans and sets, not an overall
memory bound. This still prepares and retains the full Text model and DOM,
enumerates row identities and reparents every row/source pair on reorder,
and has substantial main-thread/layout work. Native group sorting is not
interruptible; yielding is not a hard time limit. Unmounted target rendering,
duration-sort metadata, source geometry, selection/copy/printing behavior and
viewport-bounded DOM remain required work before genuine windowing can ship.

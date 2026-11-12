# Incremental Text navigation

November 12, 2026. This change removes a whole-trace navigation refresh from
ordinary local Text folds. It does not virtualize Text, shorten its input,
change trace semantics or impose a DOM/model memory bound. Regenerate existing
HTML to use it; both published sample reports include the updated runtime.

## What changes

The existing fold-range index already projects just the affected records.
However, `updateExpandableLines()` then rebuilt visible and source-expandable
position arrays by scanning every numeric row. In the real trace below,
folding 90 rows still visited all 72,176 navigation positions.

- Two Fenwick count trees represent visible rows and visible source-capable
  rows. Small visibility changes and nearest/wrapped/endpoint lookups cost
  O(log N), without materializing full candidate lists. A line-number Map
  preserves selection identity across complete presentation permutations.
- Visible/source-capable counts remain immediately available in O(1).
  Compatibility snapshots are computed and cached only when explicitly read;
  normal UI navigation does not read them. Hiding a non-source row does not
  invalidate an unchanged source-capable snapshot.
- Many changes switch to a pending linear rebuild, not N separate tree
  updates. The threshold since the previous refresh is
  `max(1, floor(N / max(1, ceil(log2(N + 1)))))`. Remaining changes update only
  visibility, counts and snapshot validity. Refresh or the next nonempty
  navigation query rebuilds both trees once before using them.
- Thus full batches retain O(N) navigation-maintenance work. Reordering also
  validates a complete permutation and rebuilds in O(N). Counts and explicit
  snapshots stay correct even before a pending rebuild. An ordinary local
  refresh does not scan the complete numeric order.
- The source-capable definition, source-free boundary fallback, filtered or
  collapsed ancestors, nearest/wrapped navigation, keyboard selection and
  original recorded line identities do not change. Both single HTML and
  mounted sidecar Text use the same model.

This is an index change, not new controls or different colors. Initial model
preparation, whole-report filters, presentation reordering and global depth
operations still do complete work. Source expansion, focus, scrolling and
layout still use DOM. The trees, full logical records and all mounted rows
remain in memory.

## Real-trace comparison

Input: `/tmp/pi4/traceB.txt`, 72,176 records / 55,186 calls. Both reports use
`--no-config --no-btf --timeline --enable-fold --filter --report-mode sidecar`,
without a vmlinux. This input has **no source-expandable rows**, so its measured
fold timings are not evidence of improved source navigation or source layout.
Model tests and source-capable browser fixtures cover those semantics.

The baseline runtime is `068a33122080`. Three alternating before/after rounds
use independent Chromium 145.0.7632.6 processes, 1500 × 900, real `fgview` HTTP. The report
is fully mounted before each fold/filter measurement. Checksums cover every
visible line, folded identity and fold summary, not only a visible screen.
Timing and RSS are recorded before diagnostic order proxies are installed;
their counts therefore do not inflate handler timing. Full filter/Clear
measurements run after the diagnostic proxies have been removed.

Both manifests' 20 resources pass byte-length and SHA-256 checks. All 18 Text
chunks and the full filtering index are byte-identical; Timeline differs only
in measured `stats.build_time`.

| Observation | Before | After |
| --- | ---: | ---: |
| Projected records per local fold/unfold | 90 | 90 |
| Numeric navigation positions visited per local fold/unfold | 72,176 | 0 |
| Four successive fold/unfold handler medians, ms | 9.7 / 8.3 / 8.9 / 8.2 | 8.0 / 7.6 / 7.4 / 7.4 |
| Corresponding two-rAF feedback medians, ms | 30.7 / 31.3 / 27.5 / 29.3 | 31.7 / 30.3 / 26.0 / 28.6 |
| Whole filter handler median, ms | 151.3 | 141.2 |
| Clear handler median, ms | 105.8 | 109.9 |
| Whole filter two-rAF feedback median, ms | 200.7 | 189.9 |
| Clear two-rAF feedback median, ms | 165.1 | 167.8 |
| Complete Text ready median, ms | 3162.5 | 3189.7 |
| Sampled renderer RSS median, MiB | 838.0 | 840.5 |

Each report's filter is applied and cleared three times. All nine observations
per side keep exactly 4,804 rows under
`duration:>10 AND NOT graph:finish_task_switch`; Clear restores all 72,176.
All six runs agree on every fold and filter visibility digest. Local folds
do not request new resources. Handler timing calls the same synchronous fold
API as the controls, not physical input delivery or complete paint latency.
Two rAF is a feedback proxy; RSS is a stage sample, not a peak or an upper
bound. Startup, Clear and some feedback measurements do not improve; this is
not a claim of universal speedup, reduced memory or completed windowing.

## Reproduce and verify

Generate both directory reports from the same trace and arguments, using the
baseline and updated runtimes respectively:

```bash
python3 funcgraph.py /path/to/traceB.txt --no-config --no-btf \
    --timeline --enable-fold --filter --report-mode sidecar --output REPORT_DIR

PYTHONPATH=tests python3 tests/benchmark_folds.py BEFORE_DIR AFTER_DIR \
    --function vfs_statx --rounds 3 \
    --filter 'duration:>10 AND NOT graph:finish_task_switch' \
    --output navigation-benchmark.json

PYTHONPATH=tests python3 -m unittest test_textmodel -v
```

The benchmark rejects differences in complete visibility/fold/summary hashes,
checks filtered DOM visibility against the full fold model, and reports the
entire dataset's visibility hash for filter/Clear comparisons. Its separate
diagnostic reports projected row and navigation-order access counts.

Model regressions cover 131,072 source-capable rows, forbidden full-snapshot
access during local lookups, immediate counts and lazy snapshots, randomized
visibility/permutation comparisons, empty/hidden/source-free populations and
non-power-of-two lengths. A 131,071-row typed-array write probe verifies that
bulk changes stop per-row tree work, rebuild once and resume local updates;
queries before explicit refresh must still be correct. Browser coverage
includes local subtree versus bulk folding, full filters/Clear, duration and
task ordering, nearest targets, selection identity, focus and viewport behavior.

The regenerated kernel and mixed samples retain exactly the same Timeline
models apart from measured `stats.build_time`. Sixteen before/after renders
cover Text/Timeline, both samples, light/dark and desktop/touch-sized viewports;
15 are pixel-identical, and one has five differing pixels with a maximum
one-unit channel delta. The public PNGs are unchanged because no visual design
changes in this round.

These tests protect the navigation optimization, not a bounded-memory viewer.
Further work still includes truly windowed DOM/source presentation and the
complete large-trace model's preparation and retained memory costs.

The complete regression run finishes with 1,127 tests, three optional
environment-dependent skips (external vmlinux/BTF, kernel `faddr2line` comparison
and extra trace paths), and no failures. Native raw decoding, the maintained
uftrace recorder and published-sample browser coverage are required in this
run. A freshly installed pure wheel outside the checkout also passes its
14-test smoke run with raw decoding and optional Python extras; only the
native-wheel-specific recorder check is skipped there. Its two updated JS
assets match the source bytes, and Twine accepts the wheel metadata.

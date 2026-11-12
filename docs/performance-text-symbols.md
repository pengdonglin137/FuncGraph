# Fewer Text punctuation nodes, with complete records

November 12, 2026. This changes generated ftrace Text markup, not trace data,
colors, filters, source addresses or the report schema. Regenerate an older
report to use it. It reduces redundant nodes in single-file and sidecar Text;
it does not implement bounded DOM or complete-model memory.

## Why these nodes are redundant

The highlighter previously wrapped every bracket and semicolon separately:

```html
<span class="hl-symbol">(</span><span class="hl-symbol">)</span><span class="hl-symbol">;</span>
```

Adjacent `()[];` characters now share their existing style span:

```html
<span class="hl-symbol">();</span>
```

No whitespace, function, argument, value or comment boundary is crossed.
Structural `{` and `}` always retain separate spans: folding replaces those
exact objects with the existing brace control and exit marker. No CSS, new
color, per-row listener or browser loading protocol is added. Structured
uftrace Text uses its separate formatter and is unaffected.

On the retained 72,176-row traceB input, 55,186 multi-character punctuation
runs eliminate 93,626 spans and their 93,626 text nodes. All 18 Text chunks
become byte-identical to the baseline when only those new runs are expanded
back into individual spans. The complete filter index remains byte-identical.
This checks all markup, attributes and rows, not just visible call counts.

## Independent measurements

Baseline report: `c2149637f3bb`, including the Worker checksum and one-chunk
lookahead optimization. Later documentation-only commits do not change that
runtime. Input SHA-256:
`5954b07778c00623e0bb7324cd5e78130bb5188463ab7a3ca061eb89b2f1ab62`.
The fixture contains 72,176 original rows, 55,186 calls, 55,213 frames and 12
tracks, without source bodies or vmlinux/BTF resolution.

Three alternating pairs use independent Chromium 145.0.7632.6 processes,
1500 × 900, through actual `fgview` HTTP. No tests or builds run concurrently.
The table uses medians; timings and sampled RSS are not guarantees.

| Observation | Before | After |
| --- | ---: | ---: |
| Initial Timeline ready, ms | 185.2 | 196.0 |
| Complete Text ready, ms | 2429.2 | 2364.3 |
| First parsed-status feedback, ms | 90.3 | 81.8 |
| Longest Text long task, ms | 175 | 165 |
| Text blocking above 50 ms, summed ms | 125 | 115 |
| Sampled Text renderer RSS, MiB | 781.5 | 757.8 |
| Verified Text HTML bytes | 88,934,530 | 86,032,124 |
| Mounted Text elements | 885,565 | 791,939 |
| Mounted Text text nodes | 1,050,658 | 957,032 |
| Original Text rows | 72,176 | 72,176 |
| Text requests / maximum concurrent chunks | 18 / 2 | 18 / 2 |

Complete Text is 64.9 ms / 2.7% sooner in this comparison; sampled RSS is
23.7 MiB lower. The direct mounted-subtree count, measured separately from
timing, confirms exactly 187,252 fewer nodes. CDP's whole-renderer node
counters include transient objects and are not used to assert that exact
difference. About 1.75 million renderer DOM nodes still remain.

Initial Timeline is 10.8 ms slower in these runs. It does not load Text and
its model/runtime are unchanged; the measurements do not establish a causal
Timeline effect. Small reports, other fonts/browsers and source-rich reports
have no performance claim here. Two-rAF/status observations are ready/feedback
proxies, not measured input-to-pixel latency. RSS is sampled, not peak or a bound.

All six complete Text model hashes match:
`bfcd594748ec635ac430b942433cff198a922f635af4ddf97573afe30fc1f9c6`.
No browser error or CSP violation occurs. No record is removed, and the
existing complete-trace filtering and analysis scope stay intact.

## Visual and behavior checks

- Unit checks preserve every character and style scope, keep braces independently
  foldable, stop runs at semantic/whitespace boundaries, and retain a 100,000-character
  punctuation run without one wrapper per character.
- A real-browser regression compares the compact leaf terminator with the exact
  original singleton markup in single/sidecar, light/dark and 390px touch/1500px
  desktop contexts. Its eight row screenshots are byte-identical; computed
  character styles and native whole-line selection agree. Browser Range glyph
  rectangles may round by up to 1/32 CSS px across the two removed boundaries;
  the test checks that bound rather than claiming identical fractional geometry.
- Seven real-kernel golden reports and regenerated `sample.html` retain their
  complete normalized output after only the exact punctuation expansion and
  existing diagnostic metadata normalization. Source snippets, addresses,
  links, parameters, folding controls and models are included in that comparison.
- `sample_mixed.html` remains byte-identical and needs no unrelated regeneration.
  The two Timeline PNGs also remain byte-identical. The refreshed Text PNG
  differs at 31 of 1,600,000 pixels, confined to one punctuation glyph's
  antialiasing; it is not claimed to be pixel-identical. Content and line layout
  remain intact.

## Validation

The final full suite runs 1,216 tests: 1,213 pass and three optional
environment-gated tests skip. Raw-input, uftrace and sample-browser checks
are required for that run. A separate matching-vmlinux BTF check and the
extra-trace check both pass, using traceB, the public I/O input and Pi 4
scheduler input; the kernel-tool comparison remains skipped, not verified.
The focused Text/parser/output/browser suite passes all 64 tests.

The pure wheel, source archive and bundled-uftrace x86_64 wheel each install
with extras in a separate environment outside the checkout. Each runs 14
installed-package checks with raw input enabled; pure/source packages
correctly skip the bundled-recorder check, while the native wheel passes all
14 with uftrace required. Twine accepts all three artifacts. The source
archive's changed code, tests and documentation are compared byte-for-byte
with the checkout. Existing unclosed-file/socket ResourceWarnings remain in
the full-suite log; there are no test failures. No release, tag or Pages
deployment is performed as part of this validation.

## Reproduce

Generate each revision's report using the same original input and options:

```bash
python3 funcgraph.py /path/to/traceB.txt --no-config --no-btf \
    --timeline --enable-fold --filter --report-mode sidecar --output REPORT_DIR
PYTHONPATH=tests python3 tests/benchmark_text_delivery.py BEFORE_DIR AFTER_DIR \
    --rounds 3 --output symbol-benchmark.json
PYTHONPATH=tests python3 -m unittest -v test_text_tree test_parser test_output \
    test_textviewport
```

Retained reports, measurements, full-chunk/golden comparisons, mounted counts
and screenshot evidence live locally under
`build/benchmarks/20261112-text-symbols/`; the large artifacts are not committed.
The next substantial work remains genuinely windowed DOM, unmounted navigation
targets, metadata retention and final layout/GC. This node reduction does not
close those obligations.

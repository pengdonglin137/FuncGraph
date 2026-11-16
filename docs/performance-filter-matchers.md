# Shared filter matcher reuse / 共享筛选正则复用

## Scope and unchanged semantics

The expression filter shared by Text and Timeline used to construct the same
case-insensitive native regex for every eligible record. An invalid pattern
such as `func:[` also threw and caught a compilation exception for each row.
This is separate from Timeline **Find** and Compare's function-name filter.

Each parsed condition now keeps its compiled matcher in a `WeakMap`, including
an explicit failed-compilation result. Every candidate is still tested: this
is not a match-result cache. Editing a condition's value invalidates its matcher;
old ASTs can be collected without a query-history cache. Frozen ASTs and their
serialized/tag representation are unchanged.

Native `RegExp(value, 'i')`, short-circuit evaluation, full recorded values,
literal dropdown choices, numeric comparisons and graph/notrace behavior stay
unchanged. Invalid regexes preserve the existing branch-specific fallback:
function names use case-sensitive literal inclusion; ordinary attributes and
returns use exact equality. Missing attributes still fail as before. The raw
return fallback remains available only after successful compilation. This does
not add new invalid-query errors, controls, counters or automatically rewritten
expressions. Single-file, deferred sidecar and Windowed Text use the same path.

共享 Text / Timeline 的表达式筛选此前对每条候选行重复编译同一正则；`func:[` 这类
非法表达式还会逐行抛出并捕获异常。现在按解析后的条件复用成功或失败的编译结果，
不缓存每行的匹配真假，不改写表达式、AST 或完整记录。旧查询可随 AST 回收，修改
条件值会重新编译。非法表达式保留原有的分支回退规则，并非新增语法报错；字面选项、
数值比较、短路求值、graph/notrace 及原始返回值匹配语义不变。界面不增加新控件。

## Complete-index comparison, 2026-11-16

Three alternating before/after pairs in Chromium **145.0.7632.6** scan the same
complete real version-4 Text index: **371,997 records**, **173,611,538 bytes**,
SHA-256 `6b6af47773f37eda35365fcac0cf026683d690b1ccb13638a4b24f1925e674e8`.
Only `assets/report/report.js` is replaced in the same complete HTML; no rows,
symbols or payloads are repeated, sampled or truncated.

Median milliseconds and compilation counts per complete scan:

| Expression | Before ms | After ms | Constructors before → after | Matching rows |
| --- | ---: | ---: | ---: | ---: |
| `func:spin` | 41.1 | 31.3 | 370,377 → 1 | 23,201 |
| `func:[` | 1,511.7 | 23.1 | 370,377 → 1 | 0 |
| `cpu:0 AND ret:-1` | 42.4 | 33.7 | 374,065 → 2 | 1,462 |
| `NOT (comm:swapper OR func:spin)` | 98.6 | 69.1 | 741,670 → 2 | 348,109 |

Preparation and one warmup are outside timing. The timed scan uses the actual
model/filter generator, drained synchronously to isolate scan CPU; cooperative
yield delivery, first-open loading, DOM updates and Timeline redraw are not
timed. A separate untimed Proxy pass counts constructors. Complete result
line sets, query text, total/matching counts and sort state are hashed outside
timing and must agree across all six runs for each query. No builds or tests
run concurrently. Full observations and exact input copies are retained locally
in `build/filter-matchers/paired.json`, `before.html`, `after.html` and
`report-before.js`; the original index remains in its capture directory.

The tracked probe is also exercised against a complete **58,453-record**
version-5 Host KVM/event index (**16,269,509 bytes**, SHA-256
`fd7ddf5e2fdfc6339576e4a648329c7e603d126ae302879c7aea54ba6c819b4a`).
One before/after pair retains all four result signatures, including the zero
function matches in this event-only input. This is a compatibility/probe check,
not a second three-pair performance claim; see local `paired-version5.json`.

这是**完整模型的纯筛选扫描**实测，不是首屏、整个 Apply 操作或帧率测试。完整结果
集合及计数逐项校验一致；计数插桩不参与计时。主要收益是消除逐行编译、尤其是非法
表达式的重复异常开销，不能据此声称所有报告或所有正则都有相同比例的提速。

## Reproduce

Save an unchanged complete baseline report and its matching `report.js`, then
provide any complete real Text index to the tracked probe:

```bash
python3 tests/benchmark_filter_matchers.py \
  --report /path/to/before.html --before-js /path/to/report-before.js \
  --index /path/to/text-index.json --rounds 3 --output /path/to/paired.json
```

The after script defaults to the working tree. The probe serves only the two
selected report versions and the supplied index on loopback. It performs no
new capture and fails if complete filter signatures differ. For meaningful
paired results, keep other tests/builds idle and use the same complete inputs.

## Validation and limits

`tests/test_filter_matchers.py` checks per-condition compilation counts, every
candidate's native regex result, invalid-pattern fallbacks, Unicode/newlines,
mutable and frozen conditions, AST/tag round trips, boolean short circuits,
numeric/exact/unknown/missing fields and raw returns. Browser checks in
`tests/test_field_filters.py` cover query edits, clearing, standalone Text,
single-file Timeline, sidecar, Windowed Text and indexed filtering before Text
is mounted. Restored records and Timeline payloads remain unchanged.

This cache **does not isolate a slow valid native regex**: a single `test()`
can still block the main thread. It adds no Worker to the shared expression
filter; Timeline Find and Compare have their own separately documented worker
paths. Full-model scans, graph construction, projection and rendering retain
their existing costs. Older HTML must be regenerated; no new recording is
required.

缓存不会隔离合法但回溯很慢的正则，单次 `test()` 仍可能阻塞页面；也不代表共享筛选
已具备 Find / Compare 的后台可取消匹配。完整模型、调用树、DOM 与绘制仍有成本。
旧 HTML 需重新生成，无需重新采集。

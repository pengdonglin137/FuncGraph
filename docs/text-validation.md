# Text 功能验收 / Text functional validation

## Scope / 范围

The Text audit checks documented behavior rather than adding controls or
sampling away trace rows. It includes filtering, source expansion, tree
folding, keyboard navigation, copying, trace-event visibility, ordering,
responsive layouts and Text/Timeline handoffs in ordinary, sidecar and
opt-in Windowed Text reports.

本轮按设计核对 Text 的过滤、源码展开、嵌套折叠、键盘导航、复制、事件隐藏、排序、
响应式布局和 Text/Timeline 往返，覆盖普通报告、sidecar 及可选 Windowed Text。
不增加控件，不用抽样或隐藏数据替代完整性验证。

## Filter correctness / 过滤准确性

- Handwritten regex, invalid-regex fallbacks and exact JSON-string dropdown
  choices remain distinct. CPU `1` must not become CPU `10` in exact mode;
  raw compiler-suffixed symbols and punctuation/Unicode names remain searchable.
- Numeric durations and hexadecimal preempt depths, all reschedule flag
  combinations, known kernel errors and classified user failures retain their
  documented matching rules. Missing values are not invented.
- `NOT` binds more tightly than `AND`, which binds more tightly than `OR`.
  Adjacent conditions are implicit `AND`; `&&`, `||` and `!` are aliases.
  Explicit and implicit conjunctions must be consumed in one precedence layer.
- `graph` includes the matching function's complete recorded subtree;
  `notrace` excludes its matching subtree. To remove `security_*` descendants
  from `vfs_read`, use `graph:vfs_read AND notrace:security_`. Adding `NOT`
  before `notrace` reverses that exclusion and instead retains those descendants.
- Apply/draft/clear, exact-value multi-selection, filter tags, sorting, source
  panels, folding, event visibility, cancellation and retry keep their existing
  behavior. Deferred filtering uses the complete index without mounting Text.
  Windowed Text keeps the complete result even when only a bounded row window
  is mounted. Timeline and analysis use the same applied line identities.

The audit found and fixed a parser defect: `cpu:="1" pid:="7" AND
comm:="worker"` previously stopped parsing after the second condition. A row
with a different command name could therefore pass. Parsing implicit and
explicit conjunctions in one loop now retains the complete expression,
including following groups, negations, disjunctions and sort directives.
The unchanged baseline fails all three independent boolean truth-table tests.

本轮发现并修复了混用空格与 `AND` 时忽略尾部条件的问题，并纠正双语指南里对
`notrace` 再取反的错误示例。独立真值表不借用被测解析器计算预期结果；浏览器验收
通过实际输入框及 Enter 应用表达式，再核对完整可见函数集合与 Timeline 共享结果。

The added oracle covers all 81 spellings of a five-condition conjunction, 48
negation/group/disjunction combinations and 12 sort-directive placements,
each over all 32 truth assignments. Five additional subtree cases cover root
returns, descendants, exclusions and missing targets. AST serialization is
evaluated again against the same independent expectations. Browser controls
cover 16 configurations (four delivery modes, desktop/touch and both themes)
and five expressions, including an empty result followed by another query.

### Complete real-index checks / 真实完整索引核对

Both unchanged real inputs are checked record by record against independently
computed Python predicates: a 371,997-record version-4 index (173,611,538 bytes)
and a 58,453-record version-5 event index (16,269,509 bytes). No index is sampled,
truncated or used as a substitute for the other. Every resulting original line
identity must agree, not merely its count.

Examples from the version-4 input:

| Expression | Old result | Correct result / independent oracle |
|---|---:|---:|
| `cpu:="0" func:spin AND duration:>5` | 7,799 | 597 |
| `func:spin NOT comm:swapper AND duration:>=1` | 23,201 | 7,672 |
| `duration:>=1 cpu:="0" AND NOT func:spin OR duration:<1 cpu:="1"` | 30,904 | 65,657 |

The defect could produce false positives or miss the later `OR` branch, not
just an inaccurate counter. The event-only index also checks CPU/PID/command
combinations with positive and zero results, plus IRQ filtering; absent
function/duration fields remain absent. All twelve complete-index result sets
match the independent oracle. These are correctness checks, not speed claims.

真实完整索引分别核对全部原始行号集合，不只比较计数。修复既消除了多选出的记录，
也恢复了之前被漏掉的后续 `OR` 分支；原始 trace 数据不变。事件输入不伪造函数名或耗时。

## Filter tag interaction / 过滤标签交互

The next interaction audit reproduced two problems in the existing controls:
remove and logic actions were non-focusable spans, and a 3,207-character literal
expanded a 1,400px desktop document to 21,331px. The same overflow occurred at
800px; compact panels wrapped labels but did not provide keyboard actions.
Baseline screenshots, geometry and an actual Tab traversal are retained in
`build/text-tag-audit/baseline.json` and `before-*.png`.

The interaction tests also found that toggling an outer `AND` to `OR` flattened
an existing grouped `OR` into separate chips. Toggling back could then change
the intended predicate. Tag parsing/serialization now retain explicit parentheses,
including same-operator and whole-expression groups, and retain compound chips
formed by operator precedence when the outer operator changes; ordinary evaluation keeps
its existing AST and precedence. `grouping-baseline.log` records eight deliberate
failures exposing the earlier behavior. The separate interrupted
`harness-directory-diagnostic.log` used unsupported directory URLs in the test
harness, not a product regression; the final tests request explicit `index.html`.

Existing actions now use native buttons with explicit accessible names. Removing
a condition focuses the next removal action, or the preceding action at the end;
the last removal focuses the input and preserves any sort directive. Logic toggles
retain focus and apply to all top-level conditions while preserving nested groups.
Restoration happens during the initiating interaction, without delayed callbacks
that could steal focus after the user moves to another control. No trace-wide pass
or new filtering model is added.

Complete labels wrap instead of truncating or widening the page. Desktop tags use
a bounded native scroll area; compact layouts keep only the existing scrolling
Filter panel. Touch actions are at least 44px, with the existing theme colors.
These controls do not alter captured records, source mappings or predicate values.

`tests/test_filter_tags.py` checks native Tab/Enter/Space, first/middle/last
removals, grouping and sorting, Text/Timeline result agreement, quoted markup as
literal text, long conditions, many conditions, native wheel scrolling and focus
after async filtering. It exercises ordinary Text-only and single-file Timeline
reports, full sidecar and opt-in Windowed Text, both themes, desktop sizes and
Chromium-emulated touch portrait/landscape. This is not a claim of real-device or
all-browser coverage. Regenerate existing HTML for these changes.

新增验证不增加筛选动作：让原有标签支持键盘与触控，长条件完整换行并在受限区域内滚动，
不省略条件、不改变原始记录，不靠延迟回调重新抢焦点。只核对有限测试场景，不能替代
所有设备和浏览器的验收。

## Referenced remote sources / 按文件补齐源码

No repository clone, checkout or source archive is needed. The existing URL
provider consumes resolved file/line positions from the debug image, fetches
only those files and merges context into the report. Repeated positions share
files in memory; subsequent runs can use the disk cache. Both the file URL and
external source-browser link should identify the revision matching the image.
Network use remains explicit through `--source-url`; this audit adds tests,
not an automatic network fallback or version-guessing policy.

The integration fixture compiles a small ELF with DWARF paths mapped to a
nonexistent build directory, removes its original source file, and runs the
real CLI and bundled fastfaddr2line without `--kernel-src`. A loopback HTTP
fixture stands in for a versioned file provider and records every request.
It verifies that only `kernel/demo.c` is requested, Text and Timeline receive
resolved source data, cached rebuilds work with the remote file unavailable,
and missing files preserve locations without fabricated code. A second image
stripped of debug information keeps symbols but yields neither invented line
locations nor unrelated download requests.

只需下载定位涉及的文件，不下载整个内核树。测试运行真实解析链路，但使用本地 HTTP
代替 GitHub，不依赖公网状态或证明某个远程分支与采集镜像相符。只有符号表、没有
DWARF 行号信息时，下载源码不能可靠补出地址到行号的映射。

### Optional cache failures / 可选缓存故障

The follow-up cache audit reproduced two failures: an unwritable cache discarded
a successful download, and providers running concurrently in the same process
shared a PID-based temporary filename. The unchanged baseline fails the three
targeted cases in `build/source-cache-audit/baseline.log`.

Cache reads now fall back to downloads on filesystem errors, and failed cache
writes retain the complete downloaded file for the current report. Unique
temporary files are closed before atomic publication; incomplete writes never
become reusable entries. Cleanup is best effort and preserves the original
warning. Cache errors remain separate from unavailable-source errors. Generation
reports one cache warning after source generation, including kernel-entry
excerpts fetched after the initial call-site preparation. Detailed paths/errors
are available with `--verbose`, without adding a new report panel or metric.

`tests/test_source_cache.py` checks file/directory collisions, denied cache reads,
full-disk publication and partial writes, cleanup denial, synchronized concurrent
providers, HTTP failures, truncated responses, recovery and offline cache hits.
The real-CLI tests additionally compare all embedded JSON payloads against
successful-cache output, verify sidecar and auto resources and their digests,
retain locations when cache and network both fail, check verbose diagnostics,
and check a structured kernel entry with no recorded call site. This entry
fixture uses FuncGraph's uftrace JSONL schema; it does not assert a new fallback
for ordinary ftrace rows lacking call sites. The original full-URL cache keys
remain unchanged, so existing cache entries are still usable.

Filesystem collisions and HTTP exchanges use real temporary files and loopback
requests; deterministic mocks inject read denial, disk-full and cleanup failures
without exhausting the host disk or changing system permissions.

缓存损坏或不可写不应让已下载源码消失，也不应把缓存问题误报成源码不存在。测试覆盖
并发发布、下载及写入中断和离线复用；不依赖公网，也不把临时文件当作完整源码。
完整验证命令与结果保存在 `build/source-cache-audit/`；该目录是本地证据，不随包发布。

Focused reproduction:

```bash
GMON_OUT_PREFIX=/tmp/funcgraph-source-cache PYTHONPATH=tests \
  python3 -m unittest test_source_cache test_remote_sources test_sources -v
```

The focused suite passes all 28 tests. Across six completed regression batches,
468 distinct tests pass and one optional comparison with the kernel's own
faddr2line is skipped because `FG_VMLINUX` / `FG_KERNEL_SRC` are not set. Ten
repeated cache tests are not counted twice. Native raw tests run with
`FG_RAW_REQUIRED=1`. `summary.json` lists every test identity, batch outcome and
sample digest. Public reports, PNGs and frontend assets remain unchanged; this
cache-only change does not require regenerating healthy existing samples.
These results overlap earlier audits and are not added to their totals.

## Reproduce / 复现

The original correctness audit on November 18, 2026 passed **1,059 distinct tests with no
skips**: 986 Text/filter/browser/input/source/documentation regressions and
73 complementary Timeline, event-only and mixed-input checks. Native raw tests
run with `FG_RAW_REQUIRED=1`; the complementary input invariants also consume
the local `write4`, host-QEMU-functions and kernel-default captures through
`FG_TRACES`. The earlier 782-test audit and later 31-test source/documentation
rerun overlap this population and are not added to the distinct total.

首轮 1,059 项不同测试全部通过、无跳过；重复运行的检查不重复计数。浏览器验收使用
Chromium 的桌面及模拟触控场景，不宣称已覆盖所有浏览器或真实移动设备。

```bash
PYTHONPATH=tests python3 -m unittest \
  test_filter_boolean test_field_filters test_filter_matchers test_filter_controls \
  test_remote_sources test_sources -v
```

The broader audit also runs the Text model/index/chunk/window/viewport, layout,
tree, anchors, copy, errno/uftrace-return and event suites, browser/sidecar/data
provider regressions, help, search toolbar and position checks. Local proof,
logs and regenerated-report fidelity observations are retained under
`build/text-functional-audit/`. These finite checks do not prove every possible
browser, regex execution time, vendor trace format or future kernel behavior.
The authoritative passing logs are `final-regressions.log`,
`input-complements.log` and `docs-source-final.log`; `validation-summary.json`
records the distinct total. `boolean-baseline-tests.log` deliberately records
the old parser's three assertion failures. Early browser-harness development
logs are superseded by the final runs, not additional product failures.

In that original audit both public reports were regenerated with the corrected shared parser. Their
five JSON payloads preserve all source data, line identities and 485/714
Timeline calls, excluding build timing only. Full HTML differs only in the
parser runtime and generation metadata. Five regenerated public/guide
screenshots remained byte-identical; that parser fix introduced no new layout or controls.

The subsequent filter-tag interaction change regenerates the same reports again.
All five JSON payloads still preserve the 485/714 calls and source/line identities,
excluding build timing only. Complete HTML differs only in the intended shared
CSS/JavaScript and generation metadata (`build/text-tag-audit/sample-fidelity.json`).
The Text screenshot is updated for the native chip controls; the other four public
and guide PNGs remain byte-identical (`screenshot-fidelity.json`). The eight
light/dark layout captures keep a 3,207-character literal inside the viewport at
1,400px, 800px, 390px portrait and 844px landscape widths, with native scrolling
instead of discarded text. Independent predicate evaluation also verifies that
two logic toggles retain truth-table matches `[13, 14, 15]`; the earlier control
incorrectly narrowed them to `[15]` (`grouping-proof.json`).

Follow-up validation passes **753 distinct tests with no skips**: 38 final
tag/boolean/literal/matcher tests, 77 final control/tree/anchor/copy/help/remote-source
tests, 237 model/window/layout/event/index/chunk tests, all 259 browser tests in
four batches, 98 sidecar/Timeline-filter tests and 44 sample/documentation/source
checks. The eight tag test methods exercise 80 browser configurations; these
subcases and earlier overlapping reruns are not counted as extra unit tests.
`validation-summary.json` records every unique test identity and the shared asset
digests. The authoritative logs are `tag-final.log`, `control-final.log`,
`model-regressions.log`, `browser-0.log` through `browser-3.log`,
`sidecar-regressions.log` and `sample-doc-tests.log`. The monolithic
`regressions.log` ended with SIGTERM (exit 143), not a completed passing run;
only the completed batches contribute to this follow-up total. These counts are
not added to the original 1,059 because the audits overlap.

后续标签交互修复完成 753 项不同测试、无跳过；另有 80 个浏览器配置子场景，不额外
累计为单元测试。原汇总进程被 SIGTERM 中断，不计作通过；以完整分批日志为准。
这次总数与首轮有重叠，不相加。新旧报告的数据与截图完整性证明保留在
`build/text-tag-audit/`。

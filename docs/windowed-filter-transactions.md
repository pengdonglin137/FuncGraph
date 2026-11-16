# Windowed Text filter preparation / 窗口化筛选准备

Updated 2026-11-16. This applies to explicitly enabled **experimental Windowed
Text** in chunked HTTP sidecar reports. It does not enable windowing by default,
change capture data or add controls. Regenerate old reports; no new recording
is required. See [windowed Text usage and limits](performance-text-window.md).

## The interaction problem

An applied windowed filter can require three separate stages: matching the
complete index, preparing a full duration order, and fetching/validating the
HTML blocks needed for the new window. Previously the new expression, result
line set and sort state were published after matching, while later stages were
still pending. The pending indicator was cleared too early. At remote-block
loading time, navigation/visible-row geometry could already describe the new
result while the old rows were still on screen.

Cancelling the shared filter did not forward its abort signal to window
rebuilding. A late sort or block response could therefore finish an operation
the user had cancelled. Error rollback existed, but did not make pending
publication and explicit cancellation coherent.

窗口化筛选不只是匹配记录，还可能准备完整耗时顺序并加载新窗口的 HTML。此前匹配
完成就发布了新表达式、命中集合并清除等待状态，导航也可能先于行内容切换。取消
筛选没有传给后续窗口重建，迟到结果可能继续生效。这不是采集解析错误，而是异步
展示阶段的状态不一致。

## Behavior

- Keep the last complete applied expression, matching line set, navigation,
  visible-row sequence and mounted rows while the new result is prepared.
  Existing pending feedback remains active through sorting and block loading.
  The existing Text status notice reports matching and row preparation as well,
  not only a callback in Timeline. Cancelling clears that operation's notice;
  late progress cannot restore it or clear a newer operation's feedback.
- Prepare candidate geometry separately. Load and validate every required
  block before synchronously replacing the visible window and its navigation.
  Only then publish the complete shared filter/counts. Source suppression and
  height invalidation occur at commit, not while the previous window is shown.
- Forward the existing filter abort signal to ranking and block reads. A new
  filter, explicit cancellation, or leaving Text cannot let a late candidate
  overwrite the last complete result. Cancellation leaves the old window
  usable for scrolling/navigation; it is not reported as zero matches.
  Internal handoffs also abort the previous controller, even when they share
  a generation. Only the current controller may publish progress or completion.
- Preparation failure retains the same applied-state object and old row nodes,
  shows the existing error feedback, and permits **Apply** to retry. A retry
  publishes its complete result once. A previously empty result remains empty
  after cancellation, rather than revealing unfiltered rows.

继续使用已有的等待 / 错误反馈，不增加整排按钮。准备期间仍展示上一份完整结果；
匹配和行准备进度直接显示在 Text 现有提示区。取消后清除旧提示，迟到进度不会
重新显示，也不会清除后续操作的状态；内部同代请求交接也取消前一个控制器。
全部排序、块加载及校验完成后，才统一切换窗口、导航和共享筛选状态。取消会传给
后续工作，失败不会清空旧结果；可重新 Apply。离开 Text 时未完成的筛选不会被
误当成已经应用，返回后仍可阅读上一份完整结果。

Direct order/fold/resize rebuilds also stage their candidate geometry until
their required blocks are ready. Their existing controls, retry and ordering
preferences remain unchanged. This is distinct from saving or applying a new
filter expression.

## Accuracy, memory and remaining costs

The full model and original nanosecond/time/argument/address/source metadata
are unchanged. No line is sampled or truncated to make preparation faster;
the existing 160-mounted-unit budget, full block validation, complete-index
matching, stable duration ordering and resource cache policies remain.

Staging temporarily retains both old and candidate geometry: maps, sequences,
visibility/height arrays and navigation state still consume memory in addition
to the full trace model. An unchanged complete sequence reuses its read-only
line-position map after comparing every item and the length. Equal-sized
sequences with different original line IDs never share that map. Matching and
visibility results are not cached by this shortcut. This is not a bounded-memory
implementation. Candidate construction, navigation validation, final DOM
projection and individual native
regex tests still have synchronous costs. Cancellation uses cooperative/native
abort handling; it does not interrupt arbitrary synchronous JavaScript or
promise a hard per-frame deadline. No universal throughput or startup speedup
is claimed.

默认完整 Text renderer 和 CLI 格式不变。保留旧状态及候选几何会增加准备期间的
临时内存；全量模型、导航验证、最终 DOM 投影及单次复杂正则仍可能阻塞。这里修复
的是展示一致性和取消传播，不宣称解决所有卡顿或降低总内存。

## Regressions

`tests/test_textwindow.py` covers pending sorted filters, forwarding cancellation
to ranking, late completion/progress, delayed remote blocks, failure/retry,
zero-match cancellation and Text/Timeline re-entry. Checks include strict
applied-state identity, complete navigation/visible-number sequences, original
mounted-node retention, pending/error state and usable subsequent navigation.
Additional checks ensure identical rebuilds do not copy a full position map,
while equally sized filters selecting different lines invalidate it correctly.
Matching/remote-load notices, cancellation cleanup, stale progress and
same-generation controller handoffs have separate browser checks.
The existing mixed/source/folding/task-order/Full Text/copy/theme/touch tests
remain in that suite. A baseline failing reproduction and final verification
artifacts are retained locally in `build/windowed-filter-atomic/`.

## Complete real-trace check

The 2026-11-16 check uses the existing complete Pi4 recording: **371,997 records**,
53,711,530 input bytes, SHA-256
`b2375370c5bafba8de3bea2cf6bd8fff67e2435a71be2505a35cc9be72411ae7`.
No DWARF/source binary was supplied, so this report has no expandable source
panels; source-rich behavior is covered separately by browser regressions.
Its version-5 Text index is **177,057,621 bytes**, SHA-256
`2ec5cd334585dc551792d4c2f285d13af8eb0d87b2831a9aa96f07ddc595285b`.

Three alternating before/after pairs use fresh Chromium 145.0.7632.6 contexts,
a 1440 × 900 viewport, HTTP `fgview` and explicit `?text=windowed`. Each pair
differs only in `window.js`; the baseline is `740c110dd18a`. All **94** data
resources are exact hard-linked files, not truncated copies. The test performs
a warm complete rebuild, applies `func:mutex` over the full model and clears
the filter. The result contains **772** matching rows; at most 160 units mount.

| Warm operation, median of three | Before | After |
| --- | ---: | ---: |
| Complete unchanged window rebuild | 47.0 ms | 36.0 ms |
| Full-index filter through row commit | 619.5 ms | 599.3 ms |

These are warm API intervals, not cold startup, physical presentation or an
input-latency guarantee. Initial download/model preparation and full-result
hashing are outside timing; browser/server caches are not forced cold. No
tests or builds run concurrently. An earlier staging-only implementation
raised warm rebuild from 47.7 to 52.8 ms; complete-sequence position-map reuse
removed that observed overhead. A separate intermediate run measured total
filter time increasing from 617.8 to 632.3 ms, so the final small improvement
must not be generalized into a universal filter speedup.

Every complete original and filtered navigation/result signature agrees across
all six contexts. Full model-row hashes, including every retained field, agree
before and after cancellation exercises. Gated ranking and remote-block checks
reproduce premature publication and late completion in the baseline; the final
runtime retains the previous state/rows, keeps pending feedback, forwards abort
and returns no applied result after cancellation. Full raw observations,
resource hashes and source identities are in `paired.json` and
`resource-identities.json` under the local artifact directory above. These are
correctness and local timing evidence, not native capture/release certification.

## Final validation and refreshed examples

The final 2026-11-16 checks cover **448 distinct passing regressions**, with no
skips, across windowing, report generation/syntax, sidecar/chunk access, viewport
behavior, event visibility, shared filters, saved views, field controls, model
semantics, anchors, copy, tree guides, layout and Text indexes. This includes
the 61 windowed-Text cases and two syntax checks; their earlier targeted run
is not added again to the count. Commands and individual test IDs are retained
in `regressions.log`, `interaction-regressions.log` and
`regression-inventory.json` in the local artifact directory.

Both public HTML examples are regenerated from their unchanged saved inputs
with Linux source commit `7475ea8b58616b33c04afb976cf0fbaad9dd63fb`, browser
revision `v7.4-rc2` and the matching local source/vmlinux paths. Every original
row attribute/text, source payload, report identity and Timeline payload agrees;
only the generated Timeline `stats.build_time` field is ignored. The kernel
sample retains 739 rows / 10 events, and the mixed sample 1,504 rows / 74 events.
All seven regenerated public/guide PNGs are byte-identical. Default full-Text
appearance and recorded user/kernel evidence therefore remain unchanged.

The portable wheel and source archive build successfully. Eighteen exact-byte
checks cover the changed runtime, related assets/modules, regression sources,
both guides, this note and its source-distribution manifest entry. An isolated
installation passes **10** smoke cases; **4** explicitly optional cases skip:
native uftrace payload, raw dependencies, resolver/highlighter extras and
tail-jump ELF dependencies/architecture. This does not certify those native
paths or replace their platform-specific release checks.

Six browser checks exercise the public examples and installed kernel/mixed
single-file and sidecar reports, with event hiding, complete shared-filter
results, view re-entry and full data restoration. Two additional installed
checks deliberately use larger **synthetic chunked fixtures** (kernel calls
with events and four timestamp-shifted repetitions of the saved mixed input),
not the small examples' normal full-Text fallback. They verify pending notice,
unchanged previous rows/state, cancellation/late replies, successful retry,
event restore and exact complete-model/Timeline preservation. These fixtures
are compatibility checks, not new recordings or performance measurements.

没有重新采集或删减样例数据。事件按钮仅影响 Text 展示，函数、丢失警告、源码及
Timeline 证据保留；用户仍可一键恢复显示。验证不增加常驻按钮、不升级版本，也不
自动执行 push、tag、Release 或 Pages 部署。

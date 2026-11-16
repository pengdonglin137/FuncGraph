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

## Event visibility while filtering

An additional interaction check reproduced a separate issue: clicking **Events:
shown / hidden** while a new filter was ranking or loading rows rebuilt the old
window and aborted the candidate. The pending expression then disappeared
silently, even though the user had changed only a presentation preference.

Event-only refreshes now retain the pending query and the complete matching
result. The previous applied window first reflects the latest event preference;
the filter then prepares its window again from the already computed match set.
It does not repeat full-index matching or publish a partial result. Rapid
show/hide changes settle to the final choice. Geometry, block projection and an
interrupted duration order may still need rebuilding; this is not a promise of
zero work or unchanged end-to-end latency after an interaction.

Explicit cancellation remains authoritative and does not wait for a slow event
refresh or cancel that separate display request. A new filter, leaving Text,
changing order/folding, or choosing Full Text cannot let an old event-driven
retry override the newer intent. Waiting uses the same abort-aware helper as
Text navigation, with listeners removed on settlement or cancellation. No new
buttons, status rows, sampled records or saved-filter semantics are introduced.

进一步修复：筛选还在排序 / 载入时切换 Events，不再悄悄丢掉待应用的条件。先完成
当前窗口的事件显隐，再复用已算好的完整命中集合准备筛选窗口；快速连点以最后一次
选择为准。取消不必等待事件加载，也不会取消独立的显隐请求。切换排序、离开 Text、
进入 Full Text 或提交新条件后，旧请求不能借显隐重试覆盖新操作。几何和被打断的
耗时排序仍可能重做，不把复用命中集合说成没有额外开销。

Seven dedicated browser cases cover matching, duration ranking, row loading,
rapid toggles, cancellation while event loading is gated, explicit order change
and Full Text handoff. They check complete result/order, one full-index match,
model/Timeline preservation and the final visibility preference. A baseline
failure against `eadd9fa6d1e5` and follow-up artifacts are retained locally under
`build/windowed-event-filter/`.

The follow-up complete Pi4 check uses the same unchanged **371,997 rows / 684
events** and 94 hard-linked data resources described below. Three alternating
fresh-context pairs compare `eadd9fa6d1e5` with the new runtime in Chromium
145.0.7632.6 at 1440 × 900. Full-index `func:mutex` results agree across all six
runs: 772 matches. Full model-row hashes, including all retained fields, agree
before/after the interleaving exercise on both versions.

Gating a sorted filter's block load and then hiding events reproduces the bug
on the baseline: the pending operation returns no applied result and the query
is empty. The fixed version applies all 772 matches in duration order, retains
the hidden choice, mounts at most 160 units and invokes full-index matching
once. This is a correctness comparison, not an elapsed-time speedup comparison
between a cancelled operation and a completed one.

Separate quiet warm API intervals measure unchanged rebuild **36.7 → 35.0 ms**
and an uninterrupted full filter **623.4 → 637.1 ms** (medians of three). The
filter's higher observed median is retained rather than presented as a speedup.
Download/model startup, full-result hashing and gated scenarios are outside
those intervals; browser/server caches are not forced cold. No tests or builds
run concurrently. Reusing matches avoids an additional full matching pass in
the interleaved scenario, not every geometry/ranking cost or all synchronous
blocking. Raw observations and exact runtime hashes are in the follow-up
`paired.json`.

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

The final event-refresh follow-up on 2026-11-16 covers **455 distinct passing regressions**, with no
skips, across windowing, report generation/syntax, sidecar/chunk access, viewport
behavior, event visibility, shared filters, saved views, field controls, model
semantics, anchors, copy, tree guides, layout and Text indexes. This includes
the 68 windowed-Text cases and two syntax checks; the earlier 85-case targeted
window/event/syntax run is not added again. The single broad run, lasting
289.498 seconds, and its individual test IDs are retained in `regressions.log`
and `regression-inventory.json` under `build/windowed-event-filter/`.

Both public HTML examples are regenerated from their unchanged saved inputs
with Linux source commit `7475ea8b58616b33c04afb976cf0fbaad9dd63fb`, browser
revision `v7.4-rc2` and the matching local source/vmlinux paths. Every original
row attribute/text, source payload, report identity and Timeline payload agrees;
only the generated Timeline `stats.build_time` field is ignored. The kernel
sample retains 739 rows / 10 events, and the mixed sample 1,504 rows / 74 events.
All seven regenerated public/guide PNGs are byte-identical in the final check.
An initial concurrent capture differed at three border pixels by one channel
level; visual/pixel inspection and a quiet official recapture of that one image
confirmed exact agreement. Both observations are retained in the artifact
directory rather than masking pixels or changing screenshot code. Default full-Text
appearance and recorded user/kernel evidence therefore remain unchanged.

The portable wheel and source archive build successfully. Nineteen exact-byte
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
also verify that hiding events during a new filter's block load preserves the
query, completes it with one matching pass and restores all data after clearing.
The fixtures contain 4,851 rows / 48 events and 6,010 rows / 296 events. They
are compatibility checks, not new recordings or performance measurements.

没有重新采集或删减样例数据。事件按钮仅影响 Text 展示，函数、丢失警告、源码及
Timeline 证据保留；用户仍可一键恢复显示。验证不增加常驻按钮、不升级版本，也不
自动执行 push、tag、Release 或 Pages 部署。

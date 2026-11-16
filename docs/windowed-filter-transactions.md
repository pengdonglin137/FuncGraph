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
- Prepare candidate geometry separately. Load and validate every required
  block before synchronously replacing the visible window and its navigation.
  Only then publish the complete shared filter/counts. Source suppression and
  height invalidation occur at commit, not while the previous window is shown.
- Forward the existing filter abort signal to ranking and block reads. A new
  filter, explicit cancellation, or leaving Text cannot let a late candidate
  overwrite the last complete result. Cancellation leaves the old window
  usable for scrolling/navigation; it is not reported as zero matches.
- Preparation failure retains the same applied-state object and old row nodes,
  shows the existing error feedback, and permits **Apply** to retry. A retry
  publishes its complete result once. A previously empty result remains empty
  after cancellation, rather than revealing unfiltered rows.

继续使用已有的等待 / 错误反馈，不增加整排按钮。准备期间仍展示上一份完整结果；
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
to the full trace model. This is not a bounded-memory implementation. Candidate
construction, navigation validation, final DOM projection and individual native
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
The existing mixed/source/folding/task-order/Full Text/copy/theme/touch tests
remain in that suite. A baseline failing reproduction and final verification
artifacts are retained locally in `build/windowed-filter-atomic/`.

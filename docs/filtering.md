# Timeline filtering / 筛选与查找

## Find or filter?

- **Find function**, in the main toolbar, highlights function-name regex matches
  and navigates between them. Enter/Shift+Enter move forward/backward and Escape
  clears the search. It only searches calls inside the active shared filter;
  it does not change that filter or the analysis scope.
- **Tools → Filter calls**, at the top of the tools dropdown, uses the same
  expression and applied result as Text and analysis. Unmatched calls are dimmed.
  **Hide unmatched tracks** additionally hides tracks without matching calls;
  the CPU event track remains available. Filtering does not rewrite call-stack
  context or the recorded data.
- Reports generated without `--filter` retain function search but do not offer
  the shared filter controls.

主工具栏的 **Find function** 只高亮并定位函数名，支持正则；Enter/Shift+Enter 前后跳转，
Escape 清除查找。它只在当前筛选结果内查找，不改变筛选条件或分析范围。

**Tools → Filter calls** 位于下拉菜单顶部，与 Text、分析共用筛选结果。未匹配调用会变暗；
**Hide unmatched tracks** 可额外隐藏没有匹配调用的轨道，CPU 事件轨道仍保留。
调用栈上下文和原始数据不会被筛选改写。未启用 `--filter` 的报告不显示这组筛选控件。

## Draft, apply and clear

1. Edit the expression. **Unapplied changes** means these edits are only a draft;
   counts, analysis and the **Applied** expression still describe the active result.
2. Choose **Apply** or press Enter to submit. Typing alone does not start a
   trace-wide filter or fetch its sidecar index. The tools menu can be closed and
   reopened without discarding the draft.
3. Escape in the filter field cancels pending work, restores the active expression
   and closes the menu. **Clear** removes both the draft and active filter; applying
   an empty expression also clears it. Clearing a filter does not clear Find.
4. A zero-match result explicitly suggests adjusting the expression or clearing
   it. **Tools · Filter on** remains visible while a call filter is active.

输入后先显示 **Unapplied changes**，不会自动扫描 trace；计数、分析和 **Applied** 仍代表
已生效条件。按 **Apply** 或 Enter 才应用。关闭并重新打开 Tools 不丢草稿。
筛选框内按 Escape 会取消未完成工作、恢复已生效条件并关闭菜单。**Clear** 同时清除草稿
和筛选；提交空条件也可以清除。查找框内容不受影响。零匹配会显示调整条件或清除的建议。

## Syntax and examples

Expand **Syntax and examples** for scope notes and a small example selector.
Selecting an example only replaces the draft; review/edit it before applying.
Examples are starting points, not a claim that the selected task or function
exists in the trace. User/kernel options follow the report's address spaces;
the duration example appears only in timed reports.

| Expression | Meaning / 含义 |
|---|---|
| `func:read\|write` | Function names matching read or write / 函数名正则匹配 |
| `pid:1234` | PID regex / PID 正则匹配 |
| `graph:vfs_read` | Matching function's whole call tree / 完整调用树 |
| `duration:>10` | Duration greater than 10 µs / 耗时超过 10 微秒 |
| `ret:all` | Known error return codes, not all returns / 已知错误返回码，不是所有返回值 |
| `space:user` / `space:kernel` | User/kernel address space / 用户态或内核态 |

Combine conditions with `AND`, `OR`, `NOT` and parentheses, for example
`space:kernel AND duration:>10`. Regex values follow the existing parser's token
rules; this UI does not add quoting or change the expression language.
See the complete [filter syntax](guide.en.md#57-unified-filter) in the usage guide.

展开 **Syntax and examples** 可查看说明并选择示例。选择只填入草稿，不自动应用；请按实际
PID、函数名调整。空间示例按报告已有用户态/内核态数据提供，计数模式不提供耗时示例。
条件支持 `AND`、`OR`、`NOT` 和括号；正则值仍遵循已有解析器规则，不新增引号语法。
完整字段见[使用指南](guide.cn.md)。

## Loading and failures

Sidecar filtering keeps its existing full-trace index, progress, cancellation and
retry behavior without mounting Text. The previous result remains active until
the new one is ready. Edits made while loading survive completion, including
after focus leaves the input. **Applied** identifies which query produced the
displayed result; **Unapplied changes** distinguishes a newer draft.

On failure, the error explains that the previous result is retained. **Retry
filter** retries the failed query only while it still matches the input. If the
draft has since changed, use **Apply** instead, so Retry cannot overwrite edits.
Older reports without an index retain their existing Text-loading fallback.

sidecar 继续按需加载完整筛选索引，保留进度、取消和重试，不为筛选挂载 Text。新结果准备好
之前，旧结果继续生效；加载期间新增的草稿，即使已离开输入框，也不会被异步完成覆盖。
失败时保留旧结果。只有输入仍是失败条件时才能 **Retry filter**；修改后应按 **Apply**，
避免重试覆盖新草稿。无索引的旧报告仍使用原有加载 Text 的回退方式。

Regenerate a report to use these controls; existing HTML and published samples
are not automatically rewritten. These changes do not add automatic filtering,
alter parser semantics or introduce another full-trace scan on each keystroke.

重新生成报告后生效，不自动改写旧 HTML 或公开 sample。本次不改变解析语义，不增加实时
筛选或每次按键的全 trace 扫描。

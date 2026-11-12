# Timeline detail layout / 详情阅读层级

The selected call keeps its usual two-column layout on wide screens and stacks
vertically on small screens. The left column prioritizes duration, self time,
start, caller site, arguments and return value, in that order when available.
Call-stack alignment, source links, user/kernel boundaries and measured data
are unchanged. This is a presentation change, not a new decoding mode.

宽屏详情保留双栏，小屏纵向排列。左栏按已有数据优先展示耗时、self、开始时间、调用位置、
参数和返回值；调用栈对齐、源码链接、用户/内核分界及测量含义不变。本次只调整阅读层级，
不增加解码或推断。

## Trace metadata

- Open **Trace metadata** below the primary facts to inspect the task/CPU,
  depth, recorded context flags, trace-line links and, when present, the recorded
  user function-entry address and raw return bits retained alongside BTF type
  information.
- This section starts collapsed. Its open/closed state is remembered in the
  browser's local storage context, including when navigating between calls or
  opening an analysis panel. Focus its summary and use Enter or Space to toggle
  it with the keyboard.
- Context/error badges, incomplete-record warnings, caller mismatches and
  tail-jump evidence remain visible outside metadata. Moving the raw flags
  does not hide the existing IRQ/preemption/atomic-context warnings.
- A recorded function-entry address is still labeled as an entry, not a caller
  site. Original bits for a BTF `void` return remain inspectable but are explicitly
  not a return value. If a value is untyped or only raw bytes were captured, it
  remains visible as raw data rather than disappearing into metadata.

点击主信息下方的 **Trace metadata** 可核对任务/CPU、深度、已有上下文 flags、trace 行号，
以及适用时的用户态函数入口地址、BTF 修正前返回位。默认折叠，浏览器会在同一存储上下文中
记住展开状态；切换函数、分析视图后仍保留。键盘聚焦标题后可用 Enter 或空格切换。

错误、未完整采集、IRQ/抢占/atomic 告警、caller 不匹配和尾跳转证据仍直接可见。入口地址
不会被改称调用地址，`void` 的原始返回位不会被伪装成有效返回值。仅有原始参数/返回数据时，
主信息仍直接展示原始数据，不因为没有类型信息而隐藏。

## Arguments and evidence / 参数与证据

- Decoded user arguments use `name=value` with recorded DWARF names. Missing
  names use explicit `slot1=value`, `slot2=value` placeholders in both Text and
  Timeline; these are captured values, not guessed source parameter names or a
  claim that every signature parameter was captured. Raw-only payloads remain
  raw bytes and are not decoded by the viewer.
- User **caller site** uses the saved caller symbol plus offset/size when
  available, based on the return PC minus one. It is not a caller-return field.
  The function's own **user return** remains available. Missing recorded symbol
  information is explicitly unavailable, not reconstructed from a same-named
  function or a nearby entry address.
- Parameters prefer wrapping between complete `name=value` items. Quoted strings
  and nested `()`, `[]` or `{}` values are not split at their internal commas.
  Recorded scalar parameter names are handled as literal names, not parsed as
  markup or separators. Malformed/unbalanced input is kept intact.
- An item longer than the available column can still wrap internally. Values
  are never shortened, rounded or replaced by an ellipsis; selecting/copying the
  parameter text retains its original values and separators.
- **Original argument bytes** and **Raw argument slots** remain next to their
  displayed parameters. Recorded user-DWARF name provenance is inside the
  original-bytes disclosure; when bytes are absent, **Argument provenance**
  provides the explanation without inventing a payload.
- Formatting only processes the selected detail's argument text. It adds no
  trace-wide traversal, symbol lookup, source fetch or duplicate per-call data.
  Source disclosure and loading retain their existing behavior.

参数优先按完整 `name=value` 项换行，不拆引号、括号内部的逗号。单项过长仍可内部换行，
不会截断、舍入或省略真实值，复制保留原有参数和分隔符。已有参数名按字面值转义，
不当成 HTML；破损输入整体保留，不猜测参数边界。

已解码用户态参数优先使用录制的 DWARF 参数名；缺名时在 Text 和 Timeline 中明确写为
`slot1=值`、`slot2=值`。这些是采集槽位，不是真实参数名，也不代表完整函数签名；不会固定
补成六个参数。仅有原始字节时继续显示 raw，不由页面猜测解码。

用户态保留 **caller site**，已记录符号时按返回 PC 减一显示为 `函数+偏移/长度`，不另列
caller return；函数本身的 **user return** 仍保留。旧记录缺少调用方符号信息时明确标为
不可用，不从同名函数或入口地址猜偏移。

**Original argument bytes / Raw argument slots** 仍紧邻参数。用户态 DWARF 参数名的来源说明
收进原始字节展开区；没有原始字节时仅提供 **Argument provenance**，不会虚构字节数据。
排版只处理当前详情，不新增全 trace 扫描、符号查找、源码请求或每条调用的重复数据。

Regenerate a report to use the new layout. Previously generated HTML files,
published samples and recorded trace files are not rewritten by this change.
Semantic colors are documented in [detail-colors.md](detail-colors.md).

重新生成报告后生效；已有 HTML、公开 sample 和原始 trace 不会被自动改写。
配色规则见 [detail-colors.md](detail-colors.md)。

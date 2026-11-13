# Timeline detail layout / 详情阅读层级

The selected call keeps its usual two-column layout on wide screens and stacks
vertically on small screens. The left column prioritizes duration, self time,
start, caller site, arguments and return value, in that order when available.
Call-stack alignment, source links, user/kernel boundaries and measured data
are unchanged. This is a presentation change, not a new decoding mode.

宽屏详情保留双栏，小屏纵向排列。左栏按已有数据优先展示耗时、self、开始时间、调用位置、
参数和返回值；调用栈对齐、源码链接、用户/内核分界及测量含义不变。本次只调整阅读层级，
不增加解码或推断。

## Selected-call actions / 所选调用操作

- **Actions** in the selected-call heading groups **Duration distribution** and
  **Open in Text** instead of repeating two links beside the symbol. Existing
  Timeline shortcuts **H** and **Enter** are unchanged.
- Click or tap the button, or focus it and press Enter, Space, Arrow Down or
  Arrow Up. In the menu, Arrow Up/Down, Home and End move between enabled actions;
  Enter or Space activates one. Escape closes the menu and restores button
  focus; Tab closes it and continues normal keyboard navigation. Horizontal
  arrow keys stay within the menu instead of panning the underlying Timeline.
- Activating an action moves focus to a visible destination: the Timeline
  control after opening the distribution, or the Text tab after switching views.
  It never leaves focus on an item inside the now-hidden menu.
- **Open in Text** is disabled with an explanation in reports generated with
  `--no-text-view`. Synthetic inline and untraced frames do not offer actions
  that imply a recorded call.
- The menu stays within the viewport, outside the detail panel's scroll clip,
  including in full screen and short landscape windows. Touch targets are at
  least 44px high. Theme colors and keyboard focus follow the existing controls.
- Opening or dismissing the menu does not traverse calls, change selection,
  alter the time range/filter, or load Text. Sidecar Text loads only when
  **Open in Text** is activated. Changing the selection, view or analysis closes
  the menu so an old action cannot operate on a different call.

所选调用标题中的 **Actions** 收纳 **Duration distribution（耗时分布）** 和
**Open in Text（跳到文本）**，不再把两个次要链接平铺在函数名旁。
原有 Timeline 快捷键 **H / Enter** 保持不变。

鼠标点击、触屏点击或聚焦按钮后按 Enter、空格、上下箭头均可打开；菜单内用上下箭头、
Home / End 切换可用项，Enter / 空格执行。Escape 仅关闭菜单并恢复按钮焦点；Tab 关闭后
继续正常移动焦点；左右箭头不会穿透菜单、平移下方的 Timeline。在全屏中，先按 Escape
关闭菜单，再按 Escape 才退出全屏。
执行操作后焦点落在可见目标：耗时分布返回 Timeline 控件，跳到文本则聚焦 Text 标签，
不会留在已经隐藏的菜单项中。

`--no-text-view` 报告将 Text 项禁用并解释原因；内联和未采集帧不会伪装为真实记录的调用。
菜单不被短详情区裁剪，保持在视口内；触屏目标至少 44px，沿用已有主题和焦点配色。
打开菜单不遍历调用、不改变选择/视野/过滤，也不预加载 Text；sidecar 仅执行跳转时加载。
切换调用、页面或分析会关闭旧菜单，防止误操作另一个调用。

## Text handoff and full-screen focus / 文本跳转与全屏焦点

**Open in Text**, the Timeline **Enter** shortcut, and recorded trace-line links
share the same focus handoff to the visible **Text** tab. Native full screen
enters and exits asynchronously: a request that finishes after navigation must
exit again and restore focus, rather than leave it on the document body or a
hidden Timeline control. Page-only full screen uses the same destination.

The handoff belongs to that exact line-navigation request. A new line request,
leaving Text, Escape/cancellation, pointer or keyboard input, or moving focus to
another control prevents the late completion from reclaiming focus. A rejected
native request releases the pending handoff. No arbitrary delay or repeated
focus timer is used; browser tests wait for the actual focus destination, not
only for `fullscreenElement` to become null.

**Open in Text**、Timeline 的 **Enter** 快捷键和真实 trace 行号链接统一把焦点交给
可见的 **Text** 标签。浏览器全屏进入/退出都是异步的：如果进入请求在跳转后才完成，
仍会退出并恢复焦点，不把键盘操作留给 body 或隐藏的 Timeline 控件。页面级全屏
使用相同目标，不改变调用选择、筛选和原始记录。

焦点恢复只属于本次跳行；切换页面、换一个目标、Escape/取消、新的鼠标或键盘输入，
以及主动聚焦其他控件都会阻止旧完成事件抢回焦点。全屏请求被拒绝后清理待恢复状态。
不靠固定延时反复强制聚焦；测试等待真实焦点目标，不能仅凭 `fullscreenElement`
已清空就认定焦点也同步恢复。旧报告需重新生成。

## Trace metadata

- Open **Trace metadata** below the primary facts to inspect the task/CPU,
  depth, trace-line links and, when present, the recorded
  user function-entry address and raw return bits retained alongside BTF type
  information.
- This section starts collapsed. Its open/closed state is remembered in the
  browser's local storage context, including when navigating between calls or
  opening an analysis panel. Focus its summary and use Enter or Space to toggle
  it with the keyboard.
- Context/error badges, incomplete-record warnings, caller mismatches and
  tail-jump evidence remain visible outside metadata. Recorded **flags** also
  appear in the primary facts, including kernel-only calls, with the raw preempt
  count and baseline explanation. The existing IRQ/preemption/atomic-context
  warnings are unchanged. Calls without recorded latency data have no flags
  row; missing flags are not presented as clear flags.
- A recorded function-entry address is still labeled as an entry, not a caller
  site. Original bits for a BTF `void` return remain inspectable but are explicitly
  not a return value. If a value is untyped or only raw bytes were captured, it
  remains visible as raw data rather than disappearing into metadata.

点击主信息下方的 **Trace metadata** 可核对任务/CPU、深度、trace 行号，
以及适用时的用户态函数入口地址、BTF 修正前返回位。默认折叠，浏览器会在同一存储上下文中
记住展开状态；切换函数、分析视图后仍保留。键盘聚焦标题后可用 Enter 或空格切换。

错误、未完整采集、IRQ/抢占/atomic 告警、caller 不匹配和尾跳转证据仍直接可见。入口地址
不会被改称调用地址，`void` 的原始返回位不会被伪装成有效返回值。仅有原始参数/返回数据时，
主信息仍直接展示原始数据，不因为没有类型信息而隐藏。

已记录的 **flags** 直接放在主信息区，纯内核调用同样可见，保留原始抢占计数及基线说明。
没有采集 latency 数据时不显示 flags 行，不把未采集误报为所有标志均未置位。

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

## Validation / 回归检查

```bash
PYTHONPATH=tests python3 -m unittest \
    test_detail_actions test_detail_expand test_timeline_fullscreen -v
```

These browser checks require Playwright and Chromium. The Actions suite covers
kernel-only, user-only and mixed reports; single/sidecar delivery; light/dark
themes; narrow, short landscape and wide touch-tablet viewports; native/page
full screen; keyboard focus and dismissal; disabled Text; stale selection and
synthetic frames. Menu-only operations must retain the model, call timestamps,
track identity, selection and time range, and must not fetch deferred Text or
Compare resources. Scroll checks require actual movement, not a synthetic event.

浏览器检查需要 Playwright 和 Chromium。Actions 用例覆盖两种交付方式、三种地址空间、
明暗主题、窄屏 / 短横屏 / 宽触屏平板、原生 / 页面全屏、键盘与触屏操作、禁用 Text、
切换选择和合成帧。只打开或关闭菜单不能改写调用时间、轨道、选择或视野，也不能提前
请求延迟加载的 Text / Compare。滚动用例验证实际位置变化，不靠伪造滚动事件通过。

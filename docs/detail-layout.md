# Timeline detail layout / 详情阅读层级

The selected call keeps its usual two-column layout on wide screens and stacks
vertically on small screens. All recorded facts share one aligned field list;
there is no metadata heading or disclosure. The reading order is timing, task
and execution context, call site and values, then low-level record references.
Call-stack alignment, source links, user/kernel boundaries and measured data
are unchanged. This is a presentation change, not a new decoding mode.

宽屏详情保留双栏，小屏纵向排列。左栏字段直接对齐展示，不再有元数据标题或折叠分组；
按“耗时 → 任务与执行上下文 → 调用位置、参数和返回值 → 底层记录”安排阅读顺序。
调用栈对齐、源码链接、用户/内核分界及测量含义不变。本次只调整阅读层级，
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
  If the menu needs internal scrolling, keyboard navigation reveals the focused
  action without scrolling the page, detail panel or tracks. Opening with Arrow
  Up also reveals the last enabled action rather than focusing it out of sight.
  Native menu scrolling remains available; at its boundaries, CSS scroll
  containment prevents a wheel gesture from spilling into the page and closing
  the menu. This uses the same containment as Tools, without canceling wheel
  events or adding global scroll interception.
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
菜单需要内部滚动时，上下箭头和 Home / End 会将聚焦项带入可见区，只滚动菜单本身，
不移动页面、详情区或轨道。用上箭头打开时，最后一个可用项也会直接可见。
保留菜单原生滚动；到达边界后采用与 Tools 相同的 CSS 滚动隔离，避免滚轮带动整页、
意外关闭菜单，不通过拦截或取消滚轮事件实现。
执行操作后焦点落在可见目标：耗时分布返回 Timeline 控件，跳到文本则聚焦 Text 标签，
不会留在已经隐藏的菜单项中。

`--no-text-view` 报告将 Text 项禁用并解释原因；内联和未采集帧不会伪装为真实记录的调用。
菜单不被短详情区裁剪，保持在视口内；触屏目标至少 44px，沿用已有主题和焦点配色。
打开菜单不遍历调用、不改变选择/视野/过滤，也不预加载 Text；sidecar 仅执行跳转时加载。
切换调用、页面或分析会关闭旧菜单，防止误操作另一个调用。

## Keyboard resizing / 键盘调整详情高度

The existing divider is a focusable horizontal separator controlling the detail
panel. Tab to it, use Up/Down for 16px adjustments, Home/End for the current
minimum/maximum, and Enter for the same default reset as double-click. A focus
outline is painted only for keyboard focus. No new button or normal-layout
space is added. Left/Right stay local instead of panning the Timeline.

Its pixel-valued minimum, maximum and current height are refreshed by normal
layout, including dragging, orientation changes, full screen, panel replacement
and restoration from temporary detail expansion. A hidden or temporarily
expanded divider is not a Tab stop. The minimum remains 60px and the chart
retains its existing 160px minimum; extreme windows can still require scrolling.
Already-clamped keyboard adjustments do not replace a preferred larger split.

Pointer and keyboard changes use the same saved fractional sizing. When storage
writes or removal fail, a session fallback keeps resizing and reset usable;
reload cannot retain an unsaved change. This also fixes the existing pointer
path: denying storage writes previously left the mixed sample at 307px after
an 80px upward drag. Successful storage behavior remains unchanged.

The browser regressions check actual Tab/arrow/Enter input, pointer parity,
reload, both themes, narrow windows, single/HTTP sidecar, kernel/mixed/event-only
reports, native and page full screen, expansion, IME/modifier/consumed keys,
storage failures, painted canvases and complete model invariants. Resize alone
does not fetch deferred Text/Compare. Accessibility attributes are checked in
Chromium; this is not a physical-device or assistive-technology compatibility
claim, nor a throughput improvement. Run `PYTHONPATH=tests python3 -m unittest
test_detail_resize -v`. Regenerate old HTML to receive the runtime changes.

The November 18 verification passes 477 distinct tests without skips: eight new
browser tests, 433 related browser/keyboard/detail/overlay/fullscreen/sample
regressions, and 36 existing pointer-resize and KVM model/browser regressions.
The public samples retain all five JSON payloads (excluding generated build
time), complete Text chunks and attributes, and 739/1,504 rows with 485/714
recorded calls. Four normal sample/detail screenshots remain byte-identical;
the help screenshot is regenerated for the updated keyboard instructions.
Real mixed-sample probes in both themes confirm the denied-storage 80px drag
now grows details from 307px to 387px, resets to 307px, retains complete recorded
data and reports no page errors. Local logs, screenshots and fidelity checks
are retained under `build/detail-ux-audit/`.

原有横条支持 Tab 聚焦、上下箭头微调、Home/End 到边界及 Enter 恢复默认；没有新增入口。
尺寸读数随布局更新，调整不会改变调用选择、时间范围、筛选或记录数据。存储受限时，
当前报告仍可调整，但未保存的变化不能跨刷新保留。详见中英文指南的底部面板说明。

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

## Field order / 字段顺序

- **Timing:** duration, self time (and percentage), start time.
- **Execution context:** task/CPU, recorded flags, and any atomic-context warning.
- **Call and values:** caller site, any static tail-jump evidence, arguments,
  then the return value. A BTF-decoded return includes a collapsed **Original
  return bits** disclosure instead of a duplicate visible **raw return** row.
- **Record references:** depth, recorded user function-entry address when
  available, and trace-line links.
- Fields appear directly in one grid without a **Trace metadata** heading or
  extra click. Old metadata open/closed preferences are no longer read or
  written; navigating, opening analysis and reloading cannot hide these fields.
  Other disclosures, such as original argument bytes and source code, are
  unchanged.
- Context/error badges, incomplete-record warnings, caller mismatches and
  tail-jump evidence remain visible. Recorded **flags** retain the raw preempt
  count and baseline explanation, including kernel-only calls. The existing
  IRQ/preemption/atomic-context warnings are unchanged. Calls without recorded
  latency data have no flags row; missing flags are not presented as clear flags.
- A recorded function-entry address is still labeled as an entry, not a caller
  site. BTF `void` is labeled **return type**, not a numeric return value;
  its original register bits remain in the same collapsed disclosure with an
  explanation. Open it with a click, Enter or Space; selecting another call
  starts collapsed again. If a value is untyped or only raw bytes were captured,
  it remains visible as raw data rather than being hidden.

所有字段在同一网格内直接展示，不再显示 **Trace metadata** 标题或折叠开关，顺序如下：

1. **时间指标**：duration、self（含占比）、start。
2. **执行上下文**：task/CPU、已记录的 flags、atomic 上下文告警（如有）。
3. **调用与数值**：caller site、静态尾跳转证据（如有）、参数、返回值；BTF 解码前的原始返回位默认折叠在 **Original return bits** 中，不再单独平铺 **raw return** 行。
4. **底层记录**：depth、用户态函数入口地址（如有）、trace 行号链接。

不再读写旧的元数据展开/折叠偏好，切换函数、打开分析、重载都不会隐藏这些字段。
参数原始字节、源码等其他折叠区保持原有行为。

错误、未完整采集、IRQ/抢占/atomic 告警、caller 不匹配和尾跳转证据仍直接可见。入口地址
不会被改称调用地址；BTF `void` 只显示为 **return type**，其原始寄存器位仍可展开核对，
并明确提示它们不是有效返回值。点击或用 Enter / 空格展开 **Original return bits**；切换调用后
恢复默认折叠。仅有原始参数/返回数据时，主信息仍直接展示原始数据，不因为没有类型信息而隐藏。

已记录的 **flags** 直接放在主信息区，纯内核调用同样可见，保留原始抢占计数及基线说明。
没有采集 latency 数据时不显示 flags 行，不把未采集误报为所有标志均未置位。

## Arguments and evidence / 参数与证据

Return-value typing, inline source chains and tail-jump analysis are independent
evidence. A caller/parent mismatch is labeled **caller mismatch**, not inferred
as inline; **tail jump (static)** requires supported `--tail-calls` evidence.
Source chains require explicit `(inlined by)` resolver markers. Multiple unrelated
positions stay separate, and their headers, source lines and links are retained.
The containing source frame is labeled **function**, not proof of a recorded entry.

返回类型、内联源码链和尾跳转各有独立证据。调用点与父函数不一致时标为 **caller mismatch**，
不默认判为内联；**tail jump (static)** 需要 `--tail-calls` 的直接跳转证据。源码只有在解析器
明确给出 `(inlined by)` 时才连成内联链，多条独立位置、标题、源码和链接不会被合并或丢弃。
源码外层标为 **function**，不等于该函数入口一定被采集。

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

Regenerate an older report to use the new layout: existing standalone HTML is
not rewritten automatically. The repository samples are refreshed with the new
runtime; their trace/source payloads and the original trace files are unchanged.
Semantic colors are documented in [detail-colors.md](detail-colors.md).

旧报告需重新生成，已有独立 HTML 不会自动更新。仓库 sample 已同步新运行时，
内嵌 trace/源码数据和原始 trace 文件均不变。
配色规则见 [detail-colors.md](detail-colors.md)。

## Validation / 回归检查

```bash
PYTHONPATH=.:tests python3 -m unittest \
    test_detail_layout test_detail_actions test_detail_colors test_detail_expand \
    test_timeline_fullscreen test_timeline_resize test_timeline_zero test_uftrace_details \
    test_source_classification test_inline test_tailcalls -v
```

These browser checks require Playwright and Chromium. The Actions suite covers
kernel-only, user-only and mixed reports; single/sidecar delivery; light/dark
themes; narrow, short landscape and wide touch-tablet viewports; native/page
full screen; keyboard focus and dismissal; disabled Text; stale selection and
synthetic frames. Menu-only operations must retain the model, call timestamps,
track identity, selection and time range, and must not fetch deferred Text or
Compare resources. Scroll checks require actual movement, not a synthetic event.
Clipped-menu checks compare the focused item's bounds with the menu's visible
client area through all navigation directions, not merely its focus identity.
They cover a 120px visual viewport with an offset and a real 160px window without
visualViewport, including native/page full screen. Pointer movement must not add
menu geometry reads.
Actual wheel input must scroll the menu in both directions, stay contained at
both boundaries, preserve outer scroll positions and leave wheel events
uncanceled. A menu whose full content already fits must also contain wheel
input. These are Chromium observations, not physical-device measurements.
The layout suite verifies exact field order in mixed and kernel-only reports,
direct visibility despite old collapsed preferences or navigation/reload,
unchanged keyboard argument disclosure, working trace links, readable raw evidence and exact
model/selection/range equality.
Zero-valued CPU/symbol indexes, BTF scalar widths and `void` return evidence
remain covered rather than being dropped as empty fields.

Return disclosures additionally cover collapsed defaults, exact 64-bit register
bits after expansion, Enter/Space, selection reset, `void`, missing values and
visible raw-only fallbacks. Source classification covers explicit/absent inline
markers, independent candidates and headers, legacy/compact positions, and
tail analysis on/off without changing recorded geometry.

浏览器检查需要 Playwright 和 Chromium。Actions 用例覆盖两种交付方式、三种地址空间、
明暗主题、窄屏 / 短横屏 / 宽触屏平板、原生 / 页面全屏、键盘与触屏操作、禁用 Text、
切换选择和合成帧。只打开或关闭菜单不能改写调用时间、轨道、选择或视野，也不能提前
请求延迟加载的 Text / Compare。滚动用例验证实际位置变化，不靠伪造滚动事件通过。
菜单聚焦检查同时核对可见边界，而不是只检查焦点身份；覆盖带偏移的 120px 可视窗口、
缺少 visualViewport 的真实 160px 窗口及两种全屏。鼠标移动不新增菜单尺寸读取。
真实滚轮输入同时验证菜单双向滚动、两端边界隔离、外层位置不变及事件未被取消；
内容已经完整容纳、不需要内部滚动时也验证边界隔离。这些是 Chromium 验证，
不代表已经在所有实体设备上测量。
布局用例还核对混合和纯内核报告的字段顺序、旧折叠偏好或跨导航/重载不隐藏字段、
参数原始字节的键盘折叠操作、trace 链接跳转、原始证据可读性，以及模型、选择和时间范围不变。
CPU/符号索引为零、BTF 标量位宽和 `void` 原始返回位均有覆盖，不会被误当成空字段丢弃。

返回值还覆盖原始位默认折叠、展开后完整 64 位值、Enter / 空格、切换调用恢复折叠、
`void`、缺失值及仅有 raw 时直接可见。源码分类覆盖明确/缺失的内联标记、独立候选与标题、
旧版/紧凑位置数据及尾调用分析开关，不改写采集几何。

2026-11-17 的本次验证：181 项相关回归、58 项源码/内联/调用栈浏览器回归、37 项发布样例与
全屏/缩放布局回归全部通过，共 276 项，非全仓库测试。重新生成的 `sample.html` 保持 739 行、
485 个真实调用、10 个事件；`sample_mixed.html` 保持 1504 行、714 个真实调用、74 个事件。
逐项比对原始行、源码池、位置索引和 Timeline 模型，除生成耗时外均一致，原生样例的尾跳转证据保留。

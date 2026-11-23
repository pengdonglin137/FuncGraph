# Timeline detail layout / 详情阅读层级

The selected recorded call keeps its two-column layout on wide screens and
stacks vertically up to 960 CSS px wide. All recorded facts share one aligned field list;
there is no general metadata heading or disclosure. The reading order is timing,
task and execution context, call site and values, then low-level record references.
When BTF provides a declaration, the arrow beside the function name opens it
directly beneath the header. The declaration is collapsed by default and never
interrupts the recorded fields. **Actions → Show recorded symbol** still shows the
exact symbol; without a BTF declaration the title arrow shows the symbol as before.
Call-stack alignment, source links, user/kernel boundaries and measured data
are unchanged. This is a presentation change, not a new decoding mode.

Compact Timeline uses a 72px label column instead of 88px, giving the values
16px more without changing the pane width or wide-screen layout. In six
`ping3` examples at 280 × 480, the recorded facts took 14–42px less height,
with no additional label wrapping. Expanded enum and raw evidence still use
the full facts width in compact layouts.

The BTF declaration uses the full detail pane width at every breakpoint.
At 1280 px the recorded facts and context remain side by side below it.
Opening a prototype scrolls the whole declaration into view when it fits
with the disclosure heading and Copy button. On a tight fit it reduces the
edge padding first; if the declaration still cannot fit, it starts at the
first line. When the viewport has just resized, opening uses the new pane
size instead of the previous split. `ping3` checks for `mprotect_fixup` and
`ip_make_skb` confirmed the complete declarations stayed visible after a
landscape resize, with the heading, Copy button, focus, and chart unchanged.

已记录调用的详情在宽屏保留双栏，宽度不超过 960 CSS 像素时纵向排列。字段直接对齐展示，不另设通用元数据折叠区；
按“耗时 → 任务与执行上下文 → 调用位置、参数和返回值 → 底层记录”安排阅读顺序。
有 BTF 原型时，点击函数名旁的箭头在标题下展开声明，默认不占空间。原始符号仍可从 **Actions → Show recorded symbol** 查看；没有原型时，标题箭头仍显示原始符号。
调用栈对齐、源码链接、用户/内核分界及测量含义不变。本次只调整阅读层级，
不增加解码或推断。

紧凑模式把标签列从 88px 缩至 72px，为值列多留 16px，不改变详情窗宽度或桌面布局。
在 280 × 480 的 `ping3` 实测中，6 个调用的字段区减少了 14–42px 高度，标签没有新增换行。
BTF 原型仍默认折叠；展开的枚举说明和原始记录仍可利用整个字段区的宽度。
同一个 `ping3` 调用在 641 CSS 像素宽的窗口中，原型的显示宽度从 260 增至 599 像素，
由三行变为一行；1280 像素宽时，字段和上下文仍并排显示。
展开原型时，详情窗会优先让标题、复制按钮和完整声明同时可见。空间刚好时先缩减边缘留白，
实在放不下才优先显示首行。若视口刚变更，按新的面板尺寸定位。`ping3` 的横屏对照测试中，
`mprotect_fixup` 和 `ip_make_skb` 的声明均完整显示，标题、复制按钮、焦点和时间轴位置不受影响。

Enum disclosures preview up to eight aliases. For more than 24, **Browse
all** opens a searchable list with at most 24 names mounted at a time.
Search covers every page, **Copy all names** ignores the filter, and the
BTF order and source of the displayed name do not change.

枚举详情先显示最多 8 个别名；超过 24 个时，**Browse all** 提供搜索和翻页，每次只渲染最多 24 个。
搜索覆盖所有页，**Copy all names** 不受筛选影响；别名顺序和显示名称的依据不变。

## Short detail panes / 短详情窗

Below 140px of interior pane height, the title and analysis column headers
scroll vertically with the content instead of covering fields or call rows.
Titles that follow horizontal scrolling still do so, keeping controls such as
Close reachable on wide tables. The rule follows the pane height, not the
window height. Expanding retains the selected call and saved split and
restores sticky headers where the viewport allows them. Sticky column headers
share one top offset measured from the title instead of setting it on every cell.

详情窗内部高度不足 140px 时，标题和分析表格列头随内容上下滚动，不再遮挡字段或调用行。
原本横向固定的标题仍然固定，宽表横向滚动时仍可操作关闭等按钮。此规则按详情窗高度生效；
展开保留所选调用和分栏比例，并按窗口条件恢复固定标题和列头。固定列头共享按标题高度计算的偏移。

On compact touch layouts, call details always show the expand icon beside
Close, even when the split is tall enough to keep the title pinned. Inlined
frames reserve room for both buttons without covering the title.

紧凑触屏布局的函数详情始终在关闭按钮旁提供展开图标，不要求先把详情窗缩小。
内联帧标题为两个按钮留出空间，不会被遮挡。

In narrow or short windows (up to 960 px wide or 480 px high), or when the
detail split itself is short, a newly selected call shows the **Call stack**
button with the recorded-frame count. Rows are built on first expansion;
closing and reopening the stack preserves the current page for the same call.
**Export stack CSV** remains available while collapsed and exports all rows.
Larger windows with a taller detail split keep the open stack, and error
chains remain visible by default. Resizing the split does not close a stack
while the reader is using it.

窗口宽度不超过 960 px、高度不超过 480 px，或详情分栏本身很短时，**Call stack** 按钮显示已记录帧数，首次展开才渲染行；鼠标和触屏行为一致。
同一次调用折叠再展开会保留当前页。折叠时也可用 **Export stack CSV** 导出完整列表。
大窗口中的高分栏仍直接显示调用栈，错误链仍默认可见。拖动分栏线不会收起正在阅读的调用栈。

In a short pane, the context pager puts the page number, Previous/Next arrows
and **Current** (when available) on one row. Screen readers still receive the
exact row range and full control names. Paging from either end of the list
reveals the whole top pager, so consecutive taps do not scroll the report or
move the Timeline. **Current** still reveals and focuses the selected frame.

短详情窗内，上下页箭头、页码和 **Current**（若可用）同排显示，读屏仍会播报准确行号范围和完整按钮名称。
从列表顶部或底部翻页后，都会显示整行顶部控件，便于连续点按，不移动报表页面或时间轴。
**Current** 仍会定位到当前帧并将焦点移到该帧。

When pagination scrolls to a table, it does so after replacing the rows,
leaving room for any sticky title and preserving the horizontal position
where the new rows allow it. In short panes, Duration and Tasks skip the
filters above the table so the new rows are visible immediately.

翻页滚动到表格时，先更新行，再为吸顶标题留出空间，并尽量保留横向滚动位置。
短窗中，耗时分布和任务表翻页时跳过表格上方的筛选栏，直接显示新页数据。

In a short Duration pane, the bucket selector, CSV export and pager stay
within reach when the call table is scrolled sideways. The pager still
scrolls vertically with the table rather than covering its rows.

耗时分布短窗横向滚动宽表时，仍可操作耗时分桶、CSV 导出和分页；分页栏随表格纵向滚动，不盖住调用行。

## Selected-call actions / 所选调用操作

- On narrow screens, the title and its controls share one row; execution
  badges use the full width underneath. At 360px and below, the recorded-call
  navigation uses shorter visual labels and tighter spacing while keeping its
  full accessible names and 44 × 44px touch targets. At 280 × 480 in `ping3`,
  all four buttons fit in one row, taking 50px less height. Larger text or a
  narrower layout can still wrap.
  In narrow portrait or short touch layouts, **Actions** uses **⋯** when
  the heading's content width is 280px or less. This follows the detail split,
  not just the window width. The accessible name and touch target are unchanged.
  Using the expand/restore button also restores the outer scroll position of
  the split when the expanded view was too short to keep that position.
  Narrow or short viewports trim whitespace around the navigation and facts
  without shrinking the 44px touch targets. Focusing a field uses the measured
  sticky heading as a scroll inset, so keyboard focus does not land behind it.
- **Actions** in the selected-call heading groups **Duration distribution** and
  **Open in Text** instead of repeating two links beside the symbol. Existing
  Timeline shortcuts **H** and **Enter** are unchanged.
- Clicking or tapping a slice focuses the chart without scrolling to it a
  second time. **Enter** opens the selected call in Text, even when Search had
  focus before selection. **Return to Timeline** restores chart focus if it is
  still visible; the search query is kept.
- Activate the selected call's title to reveal its complete recorded name in
  the scrollable body. The title stays compact and does not measure clipping on
  every selection. For clipped or abbreviated names, **Actions → Show recorded
  symbol** remains available.
  The wrapped name appears before **Copy symbol**, which copies the exact name.
  A short pane puts a 44px **←** beside the heading and first line, so leaving
  the section does not require scrolling past the name. Shift+Tab from the
  heading reaches the arrow; **Hide symbol** is the desktop label. Closing via
  the button or Escape restores the pane position and focus to the title or
  **Actions**, whichever opened it, without scrolling the chart or page.
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
- In narrow or short windows or a short detail split, **Show function prototype**,
  **Show recorded arguments**, **Show recorded return value**, **Show source excerpt**,
  and **Show call stack** appear when their data is available. Opening the menu
  does not expand sections or build the stack. The argument and return shortcuts
  focus the field label and keep enum and raw-record disclosures closed unless
  you already opened them.
  If the split is shorter than 140 CSS pixels, the prototype, recorded-value,
  and source shortcuts temporarily use the chart area so the content can be read.
  **← Call details** restores the original split and scroll position and focuses
  **Actions**, without closing the section. It is one step within the current call,
  separate from the Text/Timeline return. Where the pane stays short, a 44 × 44
  **←** button shares the destination heading's row. Shift+Tab from the heading reaches it.
  If opening a prototype, enum or raw value directly from its heading
  automatically borrows the chart area, closing that same disclosure brings
  back the split and the previous reading position. With focus inside the
  detail pane, Escape does the same before closing the call or leaving full
  screen, and returns focus to the disclosure heading. A manually expanded
  detail pane stays expanded. Returning from an Actions jump keeps a prior
  reading view only while its original disclosure is still open. If that
  disclosure was closed, or the reading view opened after the jump, returning
  brings back the chart and focuses **Actions**.
  The jump and keyboard focus use the visible part of the pane, including
  clipping by the visual viewport and the sticky header, without scrolling
  the surrounding report.

如果直接展开原型、枚举或原始值时自动借用了图表空间，焦点在详情内时按 Esc 会先收起
该项，恢复原分割和滚动位置，并把焦点还给标题。不清除选中调用，也不退出全屏。
  When an open declaration, recorded-value disclosure, or source excerpt is too
  tall to read with the top **← Call details** in view, a second return button
  appears at the end. The button uses the currently visible height, even when
  zooming or scrolling clips the pane without changing its layout size. When
  the section fits again, only the top button remains; focus moves there if
  it was on the bottom button. A source
  excerpt that arrives after the jump reveals its first visible line if focus
  is still on its heading. With focus in the jumped section, **Esc** returns
  without clearing the selection; editable fields keep their normal key handling.
  In the `ping3` touch checks, both targets remained tappable in panes only
  58–74 CSS pixels high. A direct tap restored the original pane scroll and
  Actions focus without moving the page or chart.
  After jumping to the stack in a short pane, **Call stack (N)** and **CSV**
  share that row with **←**. The accessible labels still name the recorded
  frame count and **Export stack CSV**. For a large export, **Cancel** stays
  on the row while progress appears below it.
- On desktop and touch layouts, an open recorded call's source excerpt loads
  when its heading enters the detail pane. Explicitly opening the disclosure or
  using the compact **Show source excerpt** action starts loading it immediately.
  The shortcut puts **←**, the source heading and the **chain / flat** switch on
  one row. The 44-pixel row does not grow when the source loads; narrow screens
  shorten the switch label without changing its accessible name. In
  windows at most 420 × 480 CSS pixels, the expanded source view also makes room
  by temporarily hiding chart controls and Notes. Hiding the toolbar does not
  schedule a second layout that could move the page while the excerpt loads.
  Full screen keeps its exit button and any **Return to Text** button visible.
  Returning restores the split
  and the hidden controls; if the excerpt is still loading, its arrival does
  not move the reader back into the source. The caller's source position is
  still available separately.
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

窄屏上函数名和操作按钮在同一行，执行状态标签独占下一行。360px 及以下缩短调用导航的
可见文字并收紧按钮间距，不缩短无障碍名称；触控导航按钮至少 44 × 44px。
在 280 × 480 的 `ping3` 竖屏预览中，四个导航按钮由两行变成一行，少占 50px 高度；
文字放大或可用宽度更窄时仍可换行。键盘聚焦时为吸顶标题预留实际高度，
避免原型、枚举等开关获得焦点后仍被遮挡。
窄竖屏和低矮触屏布局中，详情标题的内容宽度不超过 280px 时，**Actions** 显示为 **⋯**。
它跟随详情栏宽度，不只看窗口宽度；无障碍名称和触控面积不变。
如果展开后页面变短，点按恢复分栏会回到展开前的页面滚动位置。

Activating **Close details** returns focus to the chart, even if the detail
pane was expanded. The time range and saved split do not change.

激活 **Close details** 会关闭详情并把焦点还给时间轴，即使详情已展开占满图表区域。
时间范围和保存的分栏比例不变。

所选调用标题中的 **Actions** 收纳 **Duration distribution（耗时分布）** 和
**Open in Text（跳到文本）**，不再把两个次要链接平铺在函数名旁。
原有 Timeline 快捷键 **H / Enter** 保持不变。
窄窗或短窗还会为可用的函数原型、源码片段和调用栈提供直达选项。
激活后只滚动详情区并聚焦对应区块，不改变所选切片；打开菜单本身不会展开区块。
区块旁的 **← Call details** 恢复原来的详情滚动位置并聚焦 **Actions**，不收起已打开的内容。
这是当前调用内的一步返回，与 Text / Timeline 返回独立。短分栏中的 **←** 与区块标题同排，触控区域为 44 × 44 CSS 像素，无需先滚动。从区块标题按 Shift+Tab 也可聚焦它。
跳到调用栈后，短分栏同一行显示 **←**、**Call stack (N)** 和 **CSV**。辅助功能仍读出完整的帧数说明和导出按钮名称。大批量导出时，**Cancel** 留在同一行，进度显示在下方。
桌面和触屏都会等真实调用的源码标题进入详情窗再加载片段；手动展开或使用源码快捷入口会立即加载，调用点位置仍单独显示。
点击所选调用的标题（或聚焦后按 Enter / Space），即可在可滚动的正文中查看完整名称。
标题仍最多显示两行，切换调用时不额外测量是否截断。名称截断或省略编译器后缀时，
也可通过 **Actions → Show recorded symbol** 打开。
完整名称先于按钮显示并自动换行，短屏可以先读到正文。**Copy symbol** 原样复制名称；
点击 **Hide symbol** 或在该区域内按 Escape，可收起全文并将焦点还给打开它的标题或 **Actions**。

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
点击或点按切片后，焦点从搜索框等控件移到图表，不额外滚动页面。可直接按 Enter
打开对应文本；返回时优先聚焦仍可见的图表，原搜索内容保留。

`--no-text-view` 报告将 Text 项禁用并解释原因；内联和未采集帧不会伪装为真实记录的调用。
菜单不被短详情区裁剪，保持在视口内；触屏目标至少 44px，沿用已有主题和焦点配色。
打开菜单不遍历调用、不改变选择/视野/过滤，也不预加载 Text；sidecar 仅执行跳转时加载。
切换调用、页面或分析会关闭旧菜单，防止误操作另一个调用。

The slice header's **Close details** and **Expand details / Restore split**
buttons have 44 × 44 CSS px touch targets on wide tablets too, without
changing the compact mouse targets. Expanding and restoring keeps the
selected call and the pane size; closing returns focus to the chart.

宽触屏平板上，切片详情的**关闭**和**展开／恢复分屏**按钮也有 44 × 44 CSS px 的点击区域；
鼠标布局仍保持紧凑。恢复分屏保留所选调用和原分屏尺寸，关闭详情后焦点回到图表。

## Source on touch screens / 触屏源码详情

The source disclosure, **Source: chain / flat** button and inline frame headers
have at least 44px of height on touch screens. Long frame labels and source
paths wrap inside the panel, while code keeps its indentation and horizontal
scrolling. In both chain and flat layouts, only the code excerpt scrolls
sideways; the frame title and source link stay in place. Tab to an excerpt
and use the arrow keys to scroll without moving the Timeline. Reading or
changing the source layout does not change the selected call. Desktop mouse
layouts retain their compact control heights.

触屏上的源码开关、链式／平铺切换和内联帧标题至少高 44px。长帧名和路径在详情区内换行，
代码保留原始缩进。链式和平铺布局中，横向滚动只移动代码，帧标题和源码链接留在原位；
用 Tab 聚焦代码，再用方向键阅读，不会平移时间轴。查看源码或切换布局不改变所选调用；
鼠标桌面的控件高度仍保持紧凑。

### Reuse source excerpts on layout changes

Once chain rendering confirms a loaded excerpt has no inline chain, switching
between chain and flat keeps its nodes and scroll position. Inline excerpts
still change layout. Switching back to chain restores the frames that were
open in that excerpt; a new selection starts with its own default frame.
Unopened snippets still do not load. This applies to both Text and Timeline.
The layout preference is shared, but switching it refreshes only the active
view. The other view catches up when reopened. If the preference returns to
the already rendered layout before then, the existing excerpt is retained.

同一个源码面板从平铺切回链式时，会恢复此前展开的内联帧；选中另一个调用不会沿用此前的展开状态。
布局偏好在 Text 和 Timeline 间共享，但只刷新当前视图。回到另一视图时才应用变化；如果期间切回了原布局，则保留原源码节点。

Timeline 连续选择两个使用同一源码摘录的调用时，可以复用上一个调用已排版的摘录。新区块仍在标题可见或手动打开时才挂载；布局模式不匹配时正常渲染。只交接上一个摘录，不缓存历史面板，也不继承前一次的内联帧开合、源码滚动位置或焦点。

在同一个调用内，链式／平铺切换保留每个代码块各自的横向位置。折叠的内联帧在重新展开时恢复其位置；在 Text 改布局，再回到 Timeline 也一样。相邻调用复用小摘录后，切换布局不再重新读取源码；超过节点复用上限的摘录仍会重建，但按代码块恢复位置。

在 `ping3` 切片 3554 → 3555 → 3560 上，320 × 770 竖屏和 844 × 390 横屏的触屏实测均保留 160px 的横向偏移。链式 → 平铺 → 链式期间，旧预览读取两次并把偏移归零，新预览读取零次，并保留同一个代码节点。记录见 `build/benchmarks/20261122-source-layout/ping3-probe.log`。

手动折叠某个内联帧后再切布局，该帧也保留自己的位置。折叠整个源码区时，布局按钮仍显示当前偏好，但直到重新展开才应用布局。320 × 770 的 `ping3` 实测中，折叠帧或整个源码区后再切平铺，旧版归零，新版均保留 160px；折叠的源码区保留八个链式帧，重开后才转为平铺。记录见 `build/benchmarks/20261122-source-clicks/fold-probe.log`。

在 `ping3` 的三个共享摘录的调用（切片 3554、3555 和 3560）之间重复切换，Chromium 145、4× CPU 限速下每种布局做六组交错对照。11 次重复打开的源码读取从 11 次降到 0 次。同步准备耗时之和的中位数，1500 × 900 桌面从 29.8 ms 降到 12.4 ms，844 × 390 横屏从 30.0 ms 降到 12.8 ms。显示内容和默认展开帧一致；这不是整次切换或实际画面呈现的耗时。记录见 `build/benchmarks/20261122-source-switch/reuse-ab.log`。

In Chromium 145 with 4× CPU throttling, a stress test mounted 256 excerpts
from `ping3`, including 81 with no chain. Each toggle rebuilt 175 instead of
256. Median click-handler time went from 52.9 to 44.9 ms for flat and from
104.5 to 95.1 ms for chain. Time to two animation frames fell from 338.1 to
301.2 ms and 356.2 to 323.9 ms, respectively. This is a many-open-excerpt
stress case, not typical single-panel latency. Samples are in
`build/benchmarks/20261119-trace-jump/source-mode-noop-ab.jsonl`.
Building chains in a detached container did not improve the same workload:
the chain handler median went from 96.9 to 101.8 ms. That variant was not
adopted. See `build/benchmarks/20261119-trace-jump/source-chain-detached-ab.jsonl`.

In a separate five-pair comparison (Chromium 145, 4× CPU throttling), 256
excerpts were mounted in Text while one inline excerpt was visible in
Timeline. Switching the Timeline layout reread one excerpt instead of 176.
Handler medians fell from 47.2 to 2.7 ms for flat and from 79.7 to 2.3 ms
for chain; the two-animation-frame proxy fell from 63.5 to 27.3 ms and from
99.5 to 29.0 ms. Returning to Text after both toggles preserved the existing
markup. Returning with a changed layout moved the necessary rebuild to that
switch: its handler median rose from 156.1 to 205.6 ms. The comparison uses
real `ping3` excerpts in a synthetic heavy-open-panel case, not normal tab
switch or end-to-end input latency. Samples and runner are in
`build/benchmarks/20261119-trace-jump/source-view-layout-ab.jsonl` and
`build/benchmarks/20261119-trace-jump/source-view-layout-ab.py`.

Text now rearranges the existing source nodes for excerpts with at most 256
top-level nodes. The chained layout retains a bounded list in original flat
order, not a duplicate tree of code lines. Larger excerpts and Timeline's
separately wrapped code use the normal render path. In the same stress case,
a four-pair comparison cut the return-to-Text handler median from 208.5 to
195.9 ms and the two-frame proxy from 316.4 to 301.8 ms, without rereading
the 175 inline excerpts on return. This removes parsing work, not the cost
of laying out many visible excerpts. Updating before revealing the view was
not faster consistently and was not adopted. See
`build/benchmarks/20261119-trace-jump/source-node-resume-ab.jsonl`.

Collapsed Text excerpts keep their current nodes when the layout preference
changes and use the latest preference when reopened. In a five-pair `ping3`
stress test with 256 loaded Text excerpts and only eight expanded, returning
from Timeline with a changed layout refreshed six visible inline excerpts
and left the 169 collapsed inline excerpts alone. The return handler median
fell from 30.8 to 15.5 ms; time to two animation frames fell from 58.3 to
45.5 ms (Chromium 145, 4× CPU throttling). This does not reduce the browser's
layout cost when all excerpts are expanded: one trace of that scenario
attributed about 158 ms to synchronous style and layout under horizontal
scroll restoration. Measurements are in
`build/benchmarks/20261119-trace-jump/source-collapsed-ab.jsonl` and
`build/benchmarks/20261119-trace-jump/source-return-profile.json`.

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

**Open in Text**, the Timeline **Enter** shortcut, and recorded trace-line buttons
share the same focus handoff to the visible **Text** tab. Tab to a trace-line
button and press Enter or Space to open that exact line, including an exit
line. An untraced frame's **inside** button opens its recorded parent's detail
and moves keyboard focus to the new panel's Close button. Native full screen
enters and exits asynchronously: a request that finishes after navigation must
exit again and restore focus, rather than leave it on the document body or a
hidden Timeline control. Page-only full screen uses the same destination.

The handoff belongs to that exact line-navigation request. A new line request,
leaving Text, Escape/cancellation, pointer or keyboard input, or moving focus to
another control prevents the late completion from reclaiming focus. A rejected
native request releases the pending handoff. No arbitrary delay or repeated
focus timer is used; browser tests wait for the actual focus destination, not
only for `fullscreenElement` to become null.

**Open in Text**、Timeline 的 **Enter** 快捷键和真实 trace 行号按钮统一把焦点交给
可见的 **Text** 标签。Tab 聚焦行号按钮后，按 Enter 或空格可打开该行，包括返回行。
未跟踪帧的 **inside** 按钮打开已记录的父调用，并把焦点交给新面板的关闭按钮。
浏览器全屏进入/退出都是异步的：如果进入请求在跳转后才完成，
仍会退出并恢复焦点，不把键盘操作留给 body 或隐藏的 Timeline 控件。页面级全屏
使用相同目标，不改变调用选择、筛选和原始记录。

焦点恢复只属于本次跳行；切换页面、换一个目标、Escape/取消、新的鼠标或键盘输入，
以及主动聚焦其他控件都会阻止旧完成事件抢回焦点。全屏请求被拒绝后清理待恢复状态。
不靠固定延时反复强制聚焦；测试等待真实焦点目标，不能仅凭 `fullscreenElement`
已清空就认定焦点也同步恢复。旧报告需重新生成。

## Keep the clicked row in view / 保持选中行可见

When clicking or tapping a slice opens a hidden detail pane, Timeline scrolls
the tracks only as far as needed to keep that row in view. A row that still
fits stays in place. This does not pan time, change the zoom, or expand a
collapsed track.

With details already open, clicking a visible slice at either chart edge
keeps the track scroll position. If the new detail layout makes the chart
shorter, it scrolls only as needed to keep the clicked row visible. Explicit
navigation, such as jumping from Text or following call links, still reveals
the destination row.
In a `ping3` touch check at 844×390, one top-edge tap had shifted the tracks
by 60px with details open. It now keeps the position; 12 passes over 18 visible
calls at 4× CPU throttling kept the track and page positions and time range
unchanged. This verifies click stability, not a general latency bound.

点击或轻触切片首次打开详情时，如果面板压缩了图表并挡住选中行，Timeline 只做必要的
纵向滚动。仍然可见的行保持原位，不改变时间范围，也不展开折叠轨道。

详情已经打开时，点选图表上下边缘的可见切片不会重新居中；如果换选导致图表变矮，
才做必要的纵向滚动。Text 跳转和调用间导航仍会主动定位目标行。

In a short window, the first click can leave the detail controls below the
viewport even after the selected row is in view. The page (or the full-screen
scroll container) now scrolls only enough to expose the controls, and only
when the row can stay visible above them. This keeps the chart's minimum
height. Changing the selection with the pane already open does not scroll the
outer page. In normal view, a trace jump to Text saves the outer Timeline
scroll position for **Return to Timeline** as well as retaining the selection.
Return also restores focus to the visible source control, including the Actions
button rather than its closed menu item. If the jump started in full screen,
explicit Return restores its scroll position and re-enters the same presentation
when available; a denied browser request keeps page full screen. If the source
control is no longer visible or mounted, focus goes to the Timeline tab or
the full-screen Exit button. Manual tab switching does not restore full screen.

短窗口下，首次点开详情会在选中行仍能可见的前提下，将外层页面（全屏时为全屏容器）
滚到刚好露出详情按钮，不压缩图表。详情已打开时换选切片不会触发这次滚动。普通视图中，
跳到 Text 再按 **Return to Timeline**，也会恢复原来的外层页面滚动位置。全屏跳出时，
用返回按钮会恢复全屏和该容器的滚动位置；浏览器拒绝时使用页面内全屏，手动切换标签不恢复全屏。

Changing to a different slice starts its details at the top. The reset affects
only the detail pane, not the page, chart viewport, or splitter. Reselecting the
same slice or returning from Text keeps the detail pane's reading position.
If the pane is already at its scroll origin, switching calls, opening for the
first time, and closing then reopening skip the reset. Closing saves whether
the pane was scrolled because the browser can retain that offset when the same
pane reopens with new contents. A scrolled or hidden pane still resets when a
different call needs to start at the top.

In three alternating `ping3` comparisons at 844×390 with 4× CPU throttling,
90 taps across 18 calls produced 90 redundant scroll resets before and none
after, per run. There were still 90 layout passes for the replaced content;
median click-handler time stayed around 2.7 ms and the two-frame proxy around
31 ms. This removes the forced reset, not the layout for the new details.

换选切片时只将详情内容滚回开头，不移动外层页面、轨道视口或分割条。重复选择同一个
切片，或从 Text 返回该切片时，保留详情里的阅读位置。
详情已经在左上角时，换选、首次打开或关闭后立即重开均不再请求滚动复位。浏览器可能保留
关闭前的偏移，所以从已滚动的详情关闭后重开，或在隐藏视图里换选，仍按需复位。

### Short landscape touch screens / 横屏触控

Short landscape touch viewports use shorter toolbar labels while keeping the
full accessible names and touch targets. On first opening Timeline, if the
expanded CPU lanes would push the first call row below the visible chart,
the **CPUs** track starts collapsed. Tap its header to expand it. Resizing
or switching between Text and Timeline does not change that choice.
In the real `ping3` report at 568×320, 667×375, 760×390 and 844×390, a call
can now be selected from the first screen without scrolling.
At widths up to 640px in this layout, **Zoom in**, **Zoom out**, and **Fit trace**
are in **Tools → Zoom**. That leaves space for the search match controls and
keeps the chart visible when results arrive. Pinch-to-zoom still works on the chart.
Moving to the next or previous search result scrolls just enough to bring
the call's Actions into view when the search controls and call can stay visible.
If they cannot all fit, the report does not scroll the search controls away.

短横屏触控模式使用较短的工具栏标签，保留完整无障碍名称和触控面积。首次打开
Timeline 时，如果展开的 CPU 泳道会把首个调用行挤出可见区域，**CPUs** 默认折叠。
点按轨道标题即可展开；调整窗口大小或在 Text 和 Timeline 之间切换不会重置此选择。
真实 `ping3` 报告在 568×320、667×375、760×390 和 844×390 视口中现在无需滚动即可点选调用。
这类布局的宽度不超过 640px 时，放大、缩小和 **Fit trace** 收在 **Tools → Zoom**，
给搜索结果和前后跳转按钮留出空间。图表仍支持双指缩放。
点选上一个或下一个搜索结果时，如果搜索控件、切片和 Actions 能同时放进视口，页面只滚动所需的距离；
放不下时，不会自动把搜索控件滚出屏幕。

On short touch landscape viewports (at least 660px wide and at most 480px tall),
selecting a recorded call puts its details beside the chart. The detail fields
use one column and the chart stays interactive. Drag the divider left or right
to adjust the detail width, or use Left/Right on the focused divider. Home/End
set the limits; Enter or double-click restores the default split, as close to
equal as the 320px chart and 280px detail minimums allow. The width
ratio is saved separately from the stacked height. Expand still opens the
full detail area; restoring it returns to the saved side-by-side split.
At default text size the four call-navigation buttons fit one row even at the
280px detail limit. The buttons keep 44×44px touch targets and their full
accessible names; the layout can still wrap for larger text.
In the 844×390 `ping3` touch viewport, the navigation took 53px instead of
111px at that limit, leaving another 58px for the call facts.
At 667×375, a real `ping3` tap increased detail height from 66px to 187px
and kept the slice, duration, and Actions button visible without page scroll.
At 659px the panes still stack.
Analysis panels and synthetic call details keep their existing layout. When the
Actions menu cannot fit above or below its button, it opens to the side if
there is room, so tapping the button again can still close it.

Call navigation keeps focus on the same direction while it is available.
At an endpoint it focuses the reverse direction if available. This applies
both to button activation and to the call-navigation shortcuts pressed while
those buttons are focused. A shortcut with no target doesn't move focus.
In a short split the detail pane scrolls only as far as needed to keep the
focused navigation button visible, without scrolling the surrounding page.
If neither is available, expanded details focus the restore-split button
instead of sending focus to the hidden chart.

横屏触控视口宽度至少 660px、高度不超过 480px 时，选中已记录的调用会并排显示时间轴和详情。
详情采用单列布局，可继续点选图表中的其他调用。拖动分隔条或聚焦后按左右方向键可调宽度，
Home/End 跳到限位，Enter 或双击恢复默认比例：在图表至少 320px、详情至少 280px 的前提下尽量等宽。横屏宽度和竖屏高度分别记忆；展开详情后恢复分屏，
或从 Text 返回 Timeline，仍使用原来的横屏比例。
默认字号下，详情缩至 280px 时四个调用导航按钮仍排在一行，保留 44×44px 的触控面积和完整的无障碍名称；
字号放大时仍可换行。
在真实 `ping3` 的 844×390 Chromium 触控视口中，详情从 67px 增至 235px，耗时无需滚动就能看到。
在 667×375 下，点选后详情从 66px 增至 187px，切片、耗时和 Actions 同时可见，页面不再自动滚动；659px 仍上下排列。
点击调用导航按钮或在按钮上使用快捷键时，焦点会留在详情中；到达尽头时，会移到仍可用的反向导航按钮。
快捷键没有目标时保持原焦点。如果两个方向都不可用，展开的详情会聚焦
“恢复分屏”按钮，不会把焦点移到隐藏的图表。
分栏过矮时，只滚动详情窗以露出聚焦的导航按钮，不滚动外层页面。

## Reveal from Text / 从文本定位

**In Timeline** restores the chart if expanded details have hidden it, without
changing the saved split. The selected track row is revealed after the detail pane
has set the final chart height. In a short window the page scrolls just enough
to show both the row and the call detail controls if they fit together. When
they do not fit, it prioritizes the row. The return button stays in the sticky
tab bar. Normal tab switching and **Return to Timeline** retain the previous
detail layout.

After **In Timeline**, opening the same Text line from Timeline returns to
its original reading position if the line was visible and the filter is
unchanged. A different line, a line that was outside the Text window, or a
changed filter still opens a new jump and highlights its target if visible.

An exact trace line also frames its call in time. If the slice is already fully
in view and large enough, the current zoom stays unchanged. Shorter calls zoom
in only as needed, within the normal zoom limit. A diamond marks selected slices
that still fall below the individual drawing threshold, including zero-duration
calls; it is a position marker, not a duration. When no exact slice exists, the
notice next to **Return to Text** says whether the jump used a preceding call
and shows both line numbers when it did. It stays visible in full screen and
clears on return or tab change. **Fit trace** restores the overview.

从 Text 定位时，若展开的详情遮住图表，会恢复图表、不更改保存的分割比例。打开详情后
按图表的实际高度滚到目标行。矮窗口会滚动外层页面，尽量同时显示目标行和详情操作按钮；
放不下时优先保留目标行，顶部返回按钮仍可见。
普通标签切换及 **Return to Timeline** 保留原详情布局。

从 Text 定位后，若原行当时可见且筛选未变，在 Timeline 打开同一行等同于原位返回，
不会重新居中。不同目标、原行不在当前窗口、或筛选有变化时，仍按新跳转处理。

精确匹配行号时，如果切片已在视野内且足够宽，不改变缩放；太窄时按需放大，不超过
正常缩放上限。仍低于单独绘制阈值的选中切片（包括零耗时调用）以菱形标出位置，
不改变原始耗时。没有精确匹配时，**Return to Text** 旁保留提示：如果退回前一条调用，
同时列出两处行号；如果没有可用切片，则说明没有匹配。全屏仍可见，返回或切换标签后清除。
**Fit trace** 可恢复全局视野。

在真实 `ping3` 报告上，480×290 横屏从展开的详情跳到 Text 再定位切片，图表和返回按钮
现在同时可见；1500×900 桌面打开详情后，目标切片从图表下方滚入可见区，外层页面未滚动。
两种场景均验证返回 Text 后恢复原滚动位置。对照记录在
`build/benchmarks/20261119-trace-jump/visible-timeline.jsonl`。

同一份报告的近似匹配 `L14065 → L14064` 在 320×240 和 480×130 的触屏模拟窗口中，
精简后的提示由两行变为一行，少占 18 CSS 像素。切片和返回按钮仍可点选，Text 滚动位置和
历史记录长度不变。对照记录在 `build/benchmarks/cold-detail-layout.FCRpCs/fallback-layout.jsonl`。

在同一份 trace 的这两次跳转中，418 ns 与 79 ns 的切片从不足 0.01 CSS px 放大到
约 56 CSS px，并实际画出选中描边；原始时间、行号与返回位置不变。另验证了准确的
入口/出口行匹配及无精确切片时的提示。记录在
`build/benchmarks/20261119-trace-jump/focused-slice.jsonl` 和 `focused-exact-and-fallback.jsonl`。

Timeline 刻度在同一视野内使用统一单位，按刻度间距增加小数位；悬停时间则按每像素的
时间跨度决定精度。放不下的刻度标签会省略，网格线仍保留。底栏会避开选中耗时标签，
空间不足时优先保留 `span`，不让绝对起点覆盖耗时。调用耗时及原始数据不受影响。
同一 `ping3` 横屏跳转中，三个 `51.67 ms` 变为 `51.669 ms`、`51.670 ms`、`51.671 ms`；
`418 ns` 和 `span 3.449 us` 同时可读。桌面视野内 11 个刻度均不同且未被裁切。对照及
截图记录在 `build/benchmarks/20261119-trace-jump/`，数据文件为 `readable-ruler.jsonl`。

## Field order / 字段顺序

- **Timing:** duration, self time (and percentage), start position. When an
  absolute trace clock is available, the offset and timestamp can wrap
  separately. Each value keeps its number and unit together when it fits.
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

1. **时间指标**：duration、self（含占比）、start。有绝对时间戳时，偏移和时间戳可以各占一行；有足够空间时，数字与单位始终一起显示。
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
## Narrow wait-chain columns / 窄屏等待链

A wait-chain pane up to 640 CSS pixels wide uses a compact heading and four
columns by default. **All columns** keeps the same table, page and data while
revealing the full set of fields for horizontal browsing. The choice persists
until the report is reloaded; the full-width layout always shows all columns.
On the `ping3` 320 × 700 touch preview, the wait table shrank from 933 to 302
CSS pixels and the heading from 130 to 77 pixels. Desktop column widths and
CSV bytes were unchanged. Local evidence: `build/benchmarks/20261120-wait-compact/`.

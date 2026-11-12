# Timeline detail colors / 详情区语义配色

The detail panel uses a restrained, theme-aware palette. Color identifies the
role of text, not whether a value is correct or an operation succeeded. The same
rules apply to user and kernel calls; address-space badges and boundaries remain
separate from syntax colors. No new controls or dependencies are required.

The reading hierarchy and argument wrapping are described in
[detail-layout.md](detail-layout.md).

详情区使用低饱和、适配明暗主题的语义配色。颜色表示文字的角色，不代表数据是否正确或
操作是否成功。用户态与内核态使用同一套规则，地址空间仍通过原有标识和分界线区分。
不增加按钮、彩色底块或依赖。

| Role / 角色 | Light / 浅色 | Dark / 深色 | Treatment / 用法 |
| --- | --- | --- | --- |
| Function symbol / 函数符号 | `#365f8c` | `#a3bedf` | Title, stack, caller-site symbol / 标题、调用栈、调用位置的函数名 |
| Parameter name / 参数名 | `#37645d` | `#a0c7bd` | Recorded `name=value` labels; source types / 已有参数名、源码类型 |
| Literal or keyword / 字面量或关键字 | `#705580` | `#c1afd3` | Numbers, `NULL`, booleans and highlighted C keywords / 数字、空值、布尔值、已高亮的 C 关键字 |
| Quoted string / 引号字符串 | `#795d34` | `#d0bb98` | Quoted values and highlighted source strings / 引号包裹的值、已高亮的源码字符串 |
| Secondary information / 辅助信息 | `#66707a` | `#b0b8c2` | Field labels, offsets, raw bytes, paths and notes / 字段标签、偏移、原始字节、路径、说明 |
| Confirmed error / 已确认错误 | `#ad3434` | `#eea5a1` | Existing classified error returns and error chains / 已识别的错误返回值和错误链 |

- Metrics and ordinary prose retain the normal foreground. Zero and arbitrary
  negative numbers are not automatically colored as success or failure.
- Caller sites keep the exact `symbol+offset/size` text: the symbol is blue and
  the offset is secondary. Raw addresses and undecoded byte payloads stay neutral.
- The formatter only wraps escaped text. It does not decode bytes, infer missing
  parameter names, alter return values, or add type information. Quoted values
  are kept whole, so a comma or `name=value` inside a string is not a parameter.
- Stack source links have a subtle underline at rest and use the existing link
  accent on hover or keyboard focus. Non-clickable paths do not gain link styling.
- Existing source highlighting (`--highlight-code`) uses the same palette inside
  the detail panel. This does not enable source highlighting when it is disabled,
  change Text-view colors, or change source parsing and fetching.
- These semantic text colors are tested at a contrast ratio of at least 4.5:1 against the
  panel, row-hover and highlighted-source backgrounds in both themes. Selection
  weight, link underlines, error labels and raw-data labels remain available so
  color is not the sole signal.
- Formatting only runs for rendered detail content, including paginated context
  rows, rather than scanning the full trace or changing its serialized data.

要点：耗时等指标保持正常前景色；零值不自动变绿、负数不一律变红。调用位置保留
`函数名+偏移/长度` 原文，函数名与偏移分色。未解码字节和原始地址保持中性，不因为
加了颜色就暗示已经解析。字符串内部的逗号和等号不按参数处理，未知参数名不猜测。
路径链接默认保留细下划线，鼠标悬停和键盘聚焦时强调；普通路径不伪装成链接。
源码配色仅作用于已有高亮，不影响 Text 页，也不增加全量 trace 扫描或源码加载。

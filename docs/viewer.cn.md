# 本地报告查看器

`fgview` 是可选的本机只读 HTTP 查看器，用于打开已有报告。普通 FuncGraph HTML 仍可双击离线打开；查看器不新增第三方依赖，不生成 sidecar、不虚拟化 Text、不选择大小阈值，也不减少报告的 DOM 或浏览器内存。

## 使用

安装 FuncGraph 后：

```bash
fgview trace.html
fgview trace.html --open
fgview /path/to/dedicated-report-directory --port 9000
```

源码目录中可运行 `./fgview trace.html` 或 `python3 -m fglib.viewer trace.html`。

- 首行输出就是应打开的完整 URL，包含本次服务专用的随机路径。
- 只绑定 `127.0.0.1`。默认自动选择空闲端口，`--port 0` 含义相同；没有全网绑定参数。
- 只有 `--open` 才启动浏览器。启动浏览器失败时服务仍保持运行，可以手动打开打印的 URL。
- 按 Ctrl+C 关闭服务；旧 URL 随即失效，下次启动使用不同随机路径。
- `--help` 查看选项，`--version` 显示已安装的 FuncGraph 版本。

浏览器设置按 HTTP 源保存。若希望跨次运行复用视图、折叠和过滤设置，应使用相同的显式 `--port` 和主机名；自动端口可能改变源。HTTP 与 `file://` 的设置也各自独立，随机路径本身不会改变源。

## 单文件与目录模式

**单文件模式**只提供指定的 HTML 文件，不能通过猜测文件名访问任何兄弟文件。适用于当前的自包含报告，不会顺带暴露旁边的源码或工作目录。

**目录模式**要求存在 `index.html`，会提供所选目录下普通、非隐藏的文件，包括嵌套 JSON 资源，但不提供目录列表。必须选择独立报告目录：它不是仅允许 manifest 资源的白名单，不能指向包含其他私人文件的整个源码/工作目录。Sidecar 使用此模式读取相对资源。

## 生成 Sidecar 报告

```bash
funcgraph trace.txt --timeline --report-mode sidecar --enable-fold --filter --output trace-report
fgview trace-report --open
```

文本和二进制 raw 输入共用此输出模式；raw 仍需要原生依赖。默认 `--report-mode single`，不会按文件大小或行数自动切换。Sidecar 必须配合 `--timeline`，输出为独立目录而非文件，并通过 HTTP 打开。

- 首次 Timeline 读取完整模型，不获取 Text/Compare，不创建逐行 Text DOM。源码池及索引仍内嵌，使用时同步解析。
- 首次进入 Text、跳转 trace 行号、访问 `#L<number>` 或应用 Timeline 统一过滤时，**一次完整加载 Text**。过滤等待全部行挂载，保留当前 Timeline；不会只分析已加载部分。挂载后保留完整 DOM 并复用，不是虚拟列表或有界内存，首次 Text 挂载仍可能很慢。保存的 Text 偏好和行号锚点会直接打开 Text。
- 资源请求/校验失败可 Retry；Escape/返回可取消当前视图的等待，迟到响应不会激活旧视图。若 Text 初始化中途失败，提供 **Reload report**，不复用半初始化的监听器/状态。
- `--no-text-view` 继续省略 Text，也不生成 Text 资源；`--keep-source` 的源码解析语义不变。

产物含 `index.html`、隐藏归属标记及不可变的 `data-<report-id>/` 资源（manifest、Timeline、可选 Compare 和完整 Text），记录精确 UTF-8 字节数及 SHA-256。共享/归档必须携带**整个目录**，不能只复制 index.html。

本地 72,176 行 trace 实测，完整 Timeline 就绪中位数由 1.87s 降到 0.34s，采样 renderer RSS 由约 758 降到 202 MiB；但首次完整 Text 挂载单次仍需 8.46s。不是所有机器/trace 的性能承诺，方法、限制与可复测脚本见[设计 §8.7](design.md#87-显式-sidecar-的首次-timeline2026-11-10)。

发布先在目标同级目录完整生成、同步。新/空目录仅在全部成功后发布；拒绝非空且无标记的目录、文件目标及输出符号链接。更新已标记资源包时先发布新版本资源，最后原子替换 index.html；失败保留旧入口。旧资源目录故意保留，保证已打开页面仍能读取，所以反复更新会增加磁盘占用；清理时先关闭旧查看器/页面，再删除旧资源包目录后重新生成，或改用新的独立目录。

查看器通过打开的目录句柄固定所选根目录；不提供根下的符号链接、路径逃逸、隐藏路径、FIFO 或其他非普通文件。若显式选择的入口路径本身是符号链接，则选中其解析后的目标。平台必须支持目录相对且不跟随符号链接的文件访问；缺少能力时明确报错，不降级成不受限服务器。

## 安全与加载边界

只支持 GET/HEAD。请求必须使用有效的回环 Host 和本次随机路径；显式跨源 Origin 被拒绝，没有 CORS、上传、编辑、删除或 shell 接口。响应禁止缓存和 MIME 嗅探、限制跨源读取，并提供兼容报告现有内联脚本/样式及同源 fetch 的内容安全策略。

请只打开可信报告。报告的 JavaScript 仍会在浏览器中执行；查看器不是 HTML 净化器或安全沙箱。随机 URL 不是多用户认证，获知地址的本地用户仍可读取所选资源。不要将它当作公开报告共享服务。

启动不会解析或提前读取 Timeline、Text、源码资源；发送文件时每次读取不超过 1 MiB，HEAD 不读取正文。这只限制查看器的传输缓冲，不限制浏览器内存。完整性校验、取消和重试仍由 `fgData` 负责，普通报告依然内嵌全部数据。

## 常见问题

- **Sidecar data needs HTTP**：对包含 `index.html` 的独立报告目录运行 `fgview REPORT_DIRECTORY`，打开打印的 HTTP URL，而不是 `file://`。
- **404**：确认资源存在、是非隐藏普通文件且路径不经过符号链接。单文件模式本来就拒绝兄弟资源；完整外部资源包应使用目录模式。
- **403**：使用打印的地址，不要改成自定义域名或从其他源发起请求；这些请求被有意拒绝。
- **端口被占用**：省略 `--port` 或使用 `--port 0` 自动选择端口。
- **缺少 index.html**：目录模式不浏览文件夹；应指定单个 HTML 文件或正确的独立报告目录。
- **平台缺少安全文件访问能力**：使用支持目录相对 no-follow 访问的平台，不提供不安全回退。单文件 HTML 仍可脱离查看器离线打开。

English: [Local Report Viewer](viewer.md)。后续 sidecar 工作见[按需加载方案](plans/2026-11-10-sidecar-lazy-loading.md)。

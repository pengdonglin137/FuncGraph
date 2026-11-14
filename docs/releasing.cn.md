# FuncGraph 打包与发布操作清单

[English](releasing.md)

本文是每次发布都应逐项执行的操作清单，末尾保留已完成发布的验收记录。
当前自动化发布到 **GitHub Releases，不是 PyPI**，无需配置 PyPI token。

## 每次发布必查

- [ ] 用户已明确要求发布；选择未使用的版本，不移动已有标签。
- [ ] 审查待发布改动和工作区，保留无关文件，不用强推或清理命令覆盖他人工作。
- [ ] 更新唯一版本源 `APP_VERSION` 和 `docs/releases/vTAG.md`，同步使用文档。
- [ ] 更新两个样例 HTML、版本徽标和截图，保留真实 trace、参数、源码及内核版本对应关系。
- [ ] 核对依赖 pin 和补丁；本地测试、打包、仓库外安装验证通过，记录所有 skip 的原因。
- [ ] 明确暂存文件，写详细提交日志；先 push `master`，记录发布提交 SHA。
- [ ] 等待该 SHA 的全部 CI 检查和 `pages` job 成功，逐文件验收线上 Pages。
- [ ] 只为这个已验证的提交创建 annotated tag，再单独 push 标签。
- [ ] 等待标签的完整 Release 流水线成功；确认版本、draft/prerelease/latest 状态及六个附件。
- [ ] 下载实际发布的附件，核对 SHA-256、双架构内置 uftrace 清单及对应源码，再隔离试装。
- [ ] 记录时间、标签、提交、Actions 运行 ID、测试与跳过项、附件和 Pages 验收结果，再宣布完成。

**默认顺序：准备 → 本地验证 → push master → CI / Pages 验收 → push tag → Release 验收 → 留档。**
任一关失败都先停止，不把“已触发”“部分 job 通过”或“网页刷新了”视为发布完成。

## 最近完成的发布：v0.9.4

`v0.9.4` 已于 2026 年 11 月 14 日发布，包含保存分析现场、精确过滤草稿、内联展示优化、
详情标题统一和 BTF 编译器克隆参数安全解析，详见 [版本说明](releases/v0.9.4.md)。
发布 job 会将匹配的 `docs/releases/vTAG.md` 写入 Release 正文，再附 GitHub 生成的说明。
升版本身不会发布；仍须按下方清单核对实际标签、工作流与附件，不把源码版本当成发布成功。
已发布的 `v0.9.4`、`v0.9.3`、`v0.9.2` 标签与更早的验收记录均保留为历史，不能重新使用。

## 1. 先区分 push、标签和 Release

| 操作 | 自动执行的工作 |
| --- | --- |
| push 到 `master` | CI 测试、打包；检查通过后部署 GitHub Pages |
| 创建 PR 或手动运行 `ci` | CI 测试、打包；不创建 Release，也不部署 Pages |
| 仅 push 到 `timeline` 等功能分支 | 该 push 不会触发工作流 |
| push 一个 `v*` 版本标签 | 校验版本、测试、打包，成功后创建 GitHub Release |

**普通 push 成功不等于发布成功；有 Git 标签也不等于已经有 GitHub Release。**
“未创建版本标签，因此不会触发 Release”是指只推送了分支提交，还没有将
`vX.Y.Z` 标签推送到 GitHub。创建本地标签但不 push，也不会触发远端发布。

相关配置：

- `.github/workflows/ci.yml`：分支/PR CI 和 Pages 部署入口。
- `.github/workflows/release.yml`：标签发布入口，依次执行 `check-tag` → `build` → `publish`。
- `.github/workflows/test-and-package.yml`：两种流程共用的测试、构建和安装检查。
- `fglib/constants.py` 的 `APP_VERSION`：唯一版本源，`pyproject.toml` 自动读取。

GitHub Actions 必须启用。发布 job 使用自动提供的 `GITHUB_TOKEN`，只有该 job
具有 `contents: write` 权限，不需要额外配置 PAT。通过平时的 SSH/用户认证 Git
连接推送标签；不要改用另一个工作流的默认 `GITHUB_TOKEN` 推标签来触发新工作流。

## 2. 发布前准备

在仓库根目录、同一个 Bash 会话中依次执行后续命令，**任一步失败都应停止排查**。
需要 Python 3.8+、Git 和仓库推送权限；完整验证还需要 binutils/`addr2line`
和 C 编译器。Node.js、ShellCheck、Playwright + Chromium 用于相应的本地检查，
CI 会准备这些工具。下文的 `gh` 命令需要另外安装并登录 GitHub CLI；没有 `gh`
时，可以在仓库网页的 Actions、Releases 中检查和下载。

先确认所有待发布改动已明确；有未提交工作时先提交或妥善保存，不要直接切分支。
无关的未跟踪文件应保留并排除在提交外，不要用 `git clean`、reset 或强推来获得“干净”状态。
待发布功能必须先合入 `master`；例如改动还在 `timeline`，应先完成合并并检查结果。
以下同步命令只允许快进，遇到分叉应检查原因，不要用 reset 或强推覆盖远端。

```bash
git status --short
git fetch origin --tags
git switch master
git merge --ff-only origin/master
git log -5 --oneline
```

### 选择版本并更新版本号

以下以 **`0.9.5` 为下一次发布的示例**，并不表示已经升级或发布该版本。
每次都应先检查是否已使用；`v0.9.4` 已经发布，不能重新创建或移动到新提交。

```bash
next_version=0.9.5
release_tag="v${next_version}"
git tag --list "$release_tag"
git ls-remote --tags origin "refs/tags/$release_tag" "refs/tags/$release_tag^{}"
```

两个标签查询都应无输出；已有该标签时，请选择新的版本。
修改 `fglib/constants.py` 的 `APP_VERSION`，使其等于 `next_version`，并按需同步
`README.md`、`README.cn.md` 的版本/安装示例及 `CLAUDE.md` 的项目版本。
不要在 `pyproject.toml` 中另外维护一份固定版本。
创建匹配的 `docs/releases/$release_tag.md`，写清本次变化、兼容性、依赖和验证范围；
Release 正文中的仓库文档链接使用带该标签的完整 GitHub 地址，避免相对路径在 Release 页面失效。

支持 `X.Y.Z`、`X.Y.ZaN`、`X.Y.ZbN`、`X.Y.ZrcN`。
标签必须严格等于 `v` + `APP_VERSION`：例如版本 `0.9.2` 对应 `v0.9.2`，
版本 `0.10.0rc1` 对应 `v0.10.0rc1`。预发布版本自动标为 prerelease，且不标为 latest。

### 同步样例与截图

输出或界面有变化时，用 `samples/sample_io.txt` 和 `samples/sample_mixed.jsonl` 重新生成
两个样例；离线重放见[使用案例](examples.cn.md)，混合样例的源码增强命令见
[uftrace 指南](uftrace.cn.md)。发布样例应保留匹配的 kernel image、用户态源码、BTF/DWARF
及固定的 source-browser revision；不带源码的离线重放不能替代已有的完整样例。
检查两个 HTML 的标题与版本徽标，再刷新截图：

```bash
python3 samples/make_shots.py
python3 docs/img/make_shots.py tl_detail
```

审查 `sample.html`、`sample_mixed.html`、`sample.png`、`sample_mixed.png`、
`samples/sample_text.png` 和受影响的 `docs/img/*.png`。不能只改版本徽标而漏掉新版界面，
也不能为了更新演示删减 trace 或丢失参数、调用点和源码信息。

## 3. 本地测试与打包

打包前先执行 `python3 third_party/check_pins.py --allow-uninitialized`，核对所有
子模块 gitlink 与锁文件一致；已初始化的仓库还必须 HEAD 匹配、已跟踪源码干净。
原生构建脚本会进一步要求其所需子模块已初始化。详见
[依赖版本锁说明](../third_party/README.md)。

使用临时虚拟环境，避免修改系统 Python；输出到全新目录，避免混入旧的 `dist/` 包。

```bash
test "$(python3 -c 'from fglib.constants import APP_VERSION; print(APP_VERSION)')" = "$next_version"
build_dir="$(mktemp -d)"
python3 -m venv "$build_dir/venv"
"$build_dir/venv/bin/python" -m pip install build twine pyelftools pygments
PATH="$build_dir/venv/bin:$PATH" tests/run.sh
unset FG_UFTRACE_PREFIX
"$build_dir/venv/bin/python" -m build --outdir "$build_dir/dist"
"$build_dir/venv/bin/python" -m twine check "$build_dir/dist/funcgraph-${next_version}-py3-none-any.whl" "$build_dir/dist/funcgraph-${next_version}.tar.gz"
git diff --check
```

检查测试中的 skip 原因：本地缺少浏览器工具导致跳过，不代表完成了浏览器验证。
远端会在 Python 3.8、3.12 上测试和构建，3.12 还执行 Chromium 浏览器测试。
wheel 和源码包均经过 Twine 检查，并在仓库外的独立虚拟环境中分别验证基础安装
和 `[all]` 安装，覆盖命令入口、资源文件、报告/时间轴/对比、JSON 导出及 ELF 地址解析。
Python 3.12 的包和校验文件保存为 Actions artifact：`python-dist`。

共享工作流还在 Ubuntu 22.04 的 x86_64、aarch64 runner 上分别构建平台 wheel，
验证带补丁的导出器，移走原始构建前缀后，在仓库外安装并实际采集一个新程序。
产物分别保存为 `native-linux_ARCH-dist`。平台 wheel **内含 uftrace 和全部五种
libmcount 库**，不是仅附一个下载链接。标签为 `py3-none-linux_ARCH`，不是 manylinux；
glibc 和其他操作系统共享库仍由系统提供。依赖锁固定的是仓库提交，不是 apt 软件包版本。

在支持的 Linux 主机上，完成上述 Python-only 验证后，可复现本机原生包构建。
构建脚本不会覆盖已存在的原生输出目录：
复用缓存前还应核对上游 pin 和补丁清单；旧前缀失配时重新构建，不绕过一致性检查。

```bash
sudo apt-get install build-essential pkg-config libtraceevent-dev libelf-dev libdw-dev
git submodule update --init third_party/uftrace
FG_UFTRACE_RELEASE=1 JOBS=2 scripts/uftrace.sh build
native_prefix="$PWD/build/uftrace-$(uname -m)"
FG_UFTRACE_PREFIX="$native_prefix" "$build_dir/venv/bin/python" -m build \
    --wheel --outdir "$build_dir/native-dist"
"$build_dir/venv/bin/python" -m twine check "$build_dir/native-dist/"*.whl
mv "$native_prefix" "$build_dir/original-uftrace-prefix"
python3 -m venv "$build_dir/native-venv"
"$build_dir/native-venv/bin/python" -m pip install "$build_dir/native-dist/"*.whl
workspace="$PWD"
(cd "$build_dir" && ./native-venv/bin/python -I "$workspace/tests/smoke_installed.py" --with-uftrace)
for package in "$build_dir/native-dist/"*.whl; do
    "$build_dir/native-venv/bin/python" -m pip install "${package}[all]"
done
(cd "$build_dir" && ./native-venv/bin/python -I "$workspace/tests/smoke_installed.py" --with-uftrace --with-extras)
```

每种架构都要在对应环境验证；x86_64 测试通过不能代替 ARM64 验证。
`FG_UFTRACE_RELEASE=1` 禁用可选的 Python/Lua、curses、unwind 和 Capstone 功能。
ARM64 发布构建必须在版本信息中包含 `patchable`，且通过 `aarch64_patchable`
原生单测；导出及安装测试用 `-fpatchable-function-entry=2` 配合 `record -P .`，
不得使用会破坏首参的 `-pg`，也不能通过跳过 ARM64 参数断言来放行发布。
增强依赖检查必须带 `--with-extras`，仅安装 `[all]` 不会启用这些断言；`--fast`
源码片段检查即使没有 `gawk` 也必须通过，不得跳过源码内容断言。
原生 wheel 同时包含 GPL-2.0 `COPYING`、程序与采集库哈希、固定上游和补丁哈希，以及
`fglib/_native/uftrace/source.tar.gz`：已应用全部补丁、附构建说明的完整对应源码。
CI 还将同一源码归档单独作为附件，不能用 GitHub 自动生成的 FuncGraph 源码快照代替。

## 4. 先验收 master 和 Pages，再推标签

先审查全部 diff。以下仅暂存版本及发布文档；功能改动应在此前提交。
若本次还修改了其他待发布文件，应审查后单独加入，避免直接 `git add .` 混入无关内容。
在提交前明确暂存需要更新的样例与截图；提交后不应再有遗漏的已跟踪文件改动。

```bash
git diff
git add fglib/constants.py README.md README.cn.md CLAUDE.md docs/releasing.md docs/releasing.cn.md "docs/releases/$release_tag.md"
git commit -v
git status --short
git diff --quiet
git diff --cached --quiet
release_commit="$(git rev-parse HEAD)"
git push origin master
```

需要更新的样例与截图应审查后另外明确暂存。提交日志写清版本、改动目的、关键变化、
测试命令及结果、跳过项和样例更新范围，不能只写“update”。不要混入无关的未跟踪文件。

在 Actions 网页选中 `head_sha` 等于 `release_commit` 的分支 CI；使用 `gh` 时可先列出运行：

```bash
gh run list --workflow ci.yml --branch master --limit 5
```

找到实际运行 ID，执行 `gh run watch RUN_ID --exit-status`，或在网页等待。
必须包括 Python 3.8 / 3.12、raw、双架构 uftrace、两种 Python 的安装包检查和 `pages`，
全部成功才继续。手动触发 CI 不会部署 Pages；也不能借用其他 SHA 的绿色记录。

### 验收实际部署的 Pages

以下从该提交暂存站点，并下载线上全部七个文件比较。需要 `curl` 和 SHA-256 工具；
`sample_text.png` 在线上是根目录文件，不是仓库内的 `samples/sample_text.png` 路径。

```bash
(
set -eu
test "$(git rev-parse HEAD)" = "$release_commit"
site_dir="$(mktemp -d)"
site_url="https://pengdonglin137.github.io/FuncGraph"
bash site/build.sh "$site_dir/expected"
mkdir -p "$site_dir/online"
for page_file in index.html sample.html sample_mixed.html sample.png sample_mixed.png sample_text.png .nojekyll; do
    curl --fail --show-error --silent --location --retry 3 -H 'Cache-Control: no-cache' \
        "$site_url/$page_file" --output "$site_dir/online/$page_file"
    cmp "$site_dir/expected/$page_file" "$site_dir/online/$page_file"
done
(cd "$site_dir/online" && sha256sum -- index.html sample.html sample_mixed.html sample.png sample_mixed.png sample_text.png .nojekyll)
)
```

不一致时先核对部署提交与缓存，等待传播后重新验证，不通过移动标签或删减样例绕过。
保留比较结果和哈希清单。确认全部通过后，只给刚验证的提交打标签：

```bash
test "$(git rev-parse HEAD)" = "$release_commit"
git diff --quiet
git diff --cached --quiet
test "$(python3 -c 'from fglib.constants import APP_VERSION; print(APP_VERSION)')" = "$next_version"
test -z "$(git tag --list "$release_tag")"
test -z "$(git ls-remote --tags origin "refs/tags/$release_tag")"
git tag -a "$release_tag" "$release_commit" -m "FuncGraph $next_version"
git push origin "refs/tags/$release_tag"
```

默认不要把 master 与标签一起 `--atomic` 推送，否则会跳过标签前的 CI / Pages 验收关卡。
发布工作流先比较**标签指向的提交**中的版本，再运行共用测试/打包，最后下载
全部 `*-dist` artifacts、核对各自校验清单并合并 `SHA256SUMS`，创建带自动生成说明的 Release。任何前置 job
失败都不会发布。`master` CI（含 Pages）和标签 Release 是两次独立运行，需分别检查。

## 5. 确认真正发布成功

在 GitHub Actions 中找到本次提交对应的 `ci` 和标签对应的 `release`，等待两者成功。
也可以用 GitHub CLI 查看近期运行：

```bash
gh run list --workflow release.yml --limit 5
```

找到正确的运行 ID 后，执行 `gh run watch RUN_ID --exit-status` 等待结束；
失败时用 `gh run view RUN_ID --log-failed` 查看日志。`RUN_ID` 需替换为实际值，
不要仅凭最近一次其他提交的成功记录判断本次发布。

再检查 Releases 页面：标签正确、已发布而非 draft、正式版/预发布状态正确，
并且有以下六个附件：

- `funcgraph-<版本>-py3-none-linux_x86_64.whl`：内含采集程序的 Linux x86_64 包。
- `funcgraph-<版本>-py3-none-linux_aarch64.whl`：内含采集程序的 Linux ARM64 包。
- `funcgraph-<版本>-py3-none-any.whl`：不含原生程序的 Python wheel。
- `funcgraph-<版本>.tar.gz`：源码发布包，不是 GitHub 自动附加的源码快照。
- `funcgraph-uftrace-<版本>-source.tar.gz`：已应用全部补丁的原生对应源码。
- `SHA256SUMS`：上述五个包 / 源码归档的 SHA-256 校验值。

GitHub 自动附加的两份 Source code 快照不计入这六个附件，也不能替代原生对应源码。
正式发布还应检查它已成为预期的 latest；预发布应为 prerelease，不能错误替换正式 latest。

下载真正的 Release 附件，再校验和试装，而不只是检查本地构建结果：

```bash
workspace="$PWD"
gh release view "$release_tag"
download_dir="$(mktemp -d)"
gh release download "$release_tag" --dir "$download_dir"
(cd "$download_dir" && sha256sum --check SHA256SUMS)
python3 -m venv "$download_dir/venv"
"$download_dir/venv/bin/python" -m pip install "$download_dir/funcgraph-${next_version}-py3-none-linux_$(uname -m).whl[all]"
(cd "$download_dir" && ./venv/bin/python -I -c 'from importlib.metadata import version; print(version("funcgraph"))')
"$download_dir/venv/bin/funcgraph" --help
"$download_dir/venv/bin/fgreport" --help
env -u FG_UFTRACE -u FG_UFTRACE_PREFIX "$download_dir/venv/bin/fguftrace" --version
```

应看到全部校验都通过，安装后显示的版本等于 `next_version`。
完整复验下载包可运行仓库中的已安装包测试，`-I` 防止误导入源码目录：

```bash
(cd "$download_dir" && env -u FG_UFTRACE -u FG_UFTRACE_PREFIX ./venv/bin/python -I "$workspace/tests/smoke_installed.py" --with-extras --with-uftrace)
```

这组测试还需要 C 编译器和 binutils。源码包可在另一个全新虚拟环境中，用下载的
`funcgraph-<版本>.tar.gz` 替代原生 wheel 安装验证；Python-only 包不传 `--with-uftrace`。
通用 wheel 也要独立试装。最终记录版本、提交、Actions 运行 ID 和附件校验结果。
具备 raw 解码依赖时，再追加 `--with-raw` 检查真实发布包；没有运行的可选检查应记录为未覆盖。
还应核对两份平台 wheel 的内置 manifest、固定上游和文件哈希，以及 x86_64 wheel 中的
`fglib/_native/uftrace/source.tar.gz` 与单独发布的 uftrace 源码附件逐字节一致。

没有 `gh` 不影响发布：Git SSH 推送即可触发工作流，使用 Actions / Releases 网页检查状态，
把六个附件下载到新建的 `download_dir` 后继续校验和试装。公开 REST API 的
`actions/runs?head_sha=提交SHA`、`actions/runs/运行ID/jobs`、`releases/tags/标签` 和
`releases/latest` 也可核对对应状态，无需为了读公开结果配置 PAT。遇到 403 / 429 限流应
按 `X-RateLimit-Reset` / `Retry-After` 等待或查网页；不要高频轮询，也不要把限流误报成构建失败。

### 用户安装与使用

安装方式是下载 Release 附件后通过本地路径执行 `pip install`，如上述命令；
不是直接从 PyPI 执行 `pip install funcgraph`。
基础包没有 Python 运行时依赖；`[fast]` 安装 pyelftools，`[highlight]` 安装 Pygments，
`[all]` 安装两者。包提供 `funcgraph`、`fgreport`、`fgview`、`fgcapture`、`fguftrace`、
`fastfaddr2line` 和 `fgcapture.sh`。
平台 wheel 内含带补丁的 uftrace 和采集库，但仍需兼容的 glibc 和系统共享库；Ubuntu 上为
`libtraceevent1`、`libelf1`、`libdw1`、`liblzma5`、`libstdc++6`。Python-only 包会查找
`PATH` 中带补丁的 uftrace，也可通过 `--uftrace-bin PATH` 显式覆盖。读取 JSONL 不需要采集程序。
快速地址解析仍需相应架构的系统 `addr2line`；纯内核脚本需 Linux、Bash、root 和 tracefs。
`fgcapture --user` 不需要 root / tracefs，混合模式需要 root 和空闲的专用 tracefs。
源码运行方式 `python3 funcgraph.py`、`./fgreport` 保持可用。
两个发布包都包含解析器和 CSS/JavaScript；仓库资源在 `assets/`，wheel 内在 `fglib/_assets/`。

| Release 附件 | 内置 uftrace | 推荐用途 |
| --- | --- | --- |
| `*-linux_x86_64.whl` | 有，可执行文件及五种 libmcount | x86_64 Linux 用户态 / 混合采集 |
| `*-linux_aarch64.whl` | 有，可执行文件及五种 libmcount | ARM64 Linux 用户态 / 混合采集 |
| `*-py3-none-any.whl`、Python 源码包 | 无 | 已导出记录的离线报告，或另行提供带补丁 uftrace |
| `funcgraph-uftrace-*-source.tar.gz` | 对应源码，不是可执行安装包 | 审计 / 重建原生组件 |

安装后执行 `fguftrace --version` 核对内置程序；三种模式的编译、采集、生成报告及
产物保留方式见[统一采集案例](capture.cn.md)，实际应用和依赖库见 [pigz + zlib](pigz.cn.md)。

## 6. 常见问题与处理

sample 更新走分支 CI，不依赖版本标签。只有共享检查**全部通过**（包括 native raw 构建和打包），Pages 才会部署；不能仅凭 Python 测试通过判断更新成功。raw 构建失败时，Pages 会跳过，继续提供上一次成功部署的 sample 和截图。应检查失败 job，源码问题需要提交并推送修复，再核实新 push 对应的 `pages` job 和线上文件。重跑旧提交不会包含后续修复；手动 `workflow_dispatch` 运行 CI 也不会部署 Pages。请将线上 `sample.html`、`sample.png`、`sample_text.png`、`sample_mixed.html`、`sample_mixed.png` 与实际部署提交里的文件比较，不要把刷新浏览器当作部署验收。

| 现象 | 原因与处理 |
| --- | --- |
| push `timeline` 后没有 CI | 没有 PR 时属预期；创建 PR 或手动运行 `ci`，不能把“没有运行”当作“通过”。 |
| `master` CI 通过，但没有 Release | 普通分支 push 不发布；检查匹配标签是否已推送到 `origin`，以及标签的 `release` 运行状态。 |
| `check-tag` 失败 | 检查标签提交里的 `APP_VERSION` 是否与标签严格一致，以及版本格式是否支持。 |
| 测试、构建或资源检查失败 | 查看具体 job 日志；源码目录能运行不代表安装包包含了所有文件。 |
| artifact、校验或发布权限出错 | 检查 `build`/`publish` 日志、`python-dist` 和两份 `native-linux_ARCH-dist` artifact 及仓库/组织 Actions 策略；发布 job 需要 `contents: write`。 |
| 原生程序缺少共享库，或 wheel 不受支持 | 安装文档列出的系统运行库，选择正确架构和兼容的 Linux/glibc 主机；不能将平台 wheel 改名为通用包或 manylinux。 |
| runner、网络或服务临时失败 | 确认尚未创建 Release 后，在 Actions 选择 Re-run failed jobs 或 Re-run all jobs。 |
| 必须修改源码或工作流才能修复 | 重跑仍使用原标签提交，不会使用后来修改的 `master`；提交修复、选择新版本和标签，重新发布。 |
| 标签或 Release 已存在 | 不覆盖已发布附件，不移动已发布标签；先核实旧版本，修复以新版本发布。 |
| 原生前缀的 pin / 补丁校验不匹配 | 不复用旧缓存、不绕过参数断言；使用本次固定依赖和补丁重新构建。 |

## 7. 发布验收留档

发布完成后，将以下信息补入本文和英文版的版本记录；记录本身可以是之后的文档提交，
但不能移动已经发布的标签来纳入留档。它不需要再升版本或重新发布。

```text
版本 / UTC 发布时间：
标签对象 / 发布提交 SHA：
CI 运行 ID / head_sha / 全部 job 与 Pages 结果：
Release 运行 ID / head_sha / 全部 job 与 publish 结果：
本地测试命令、计数、所有 skip 原因：
六个附件、SHA256SUMS 验证、原生 manifest / 对应源码结果：
实际下载包的隔离安装：架构、extras、raw、uftrace、测试结果：
Pages 部署提交、七文件比较和哈希结果：
未覆盖项、失败及重试原因（若有）：
```

### 已验证：v0.9.4

| 项目 | 已验证结果 |
| --- | --- |
| 发布时间 | 2026-11-14 06:41:47 UTC；[正式 Release](https://github.com/pengdonglin137/FuncGraph/releases/tag/v0.9.4) |
| 发布提交 | `e05e9280f1f072c1cbe1fd6689f295def7acce3e` |
| Annotated tag 对象 | `v0.9.4` → `a886cf2c4e0ae214e60776c72bfa533fa51d29db`，解析到上述发布提交 |
| CI / Pages | [运行 43563486436](https://github.com/pengdonglin137/FuncGraph/actions/runs/43563486436)，发布提交的 8 个 job 全部成功，包含 Pages 部署 |
| Release | [运行 43565377158](https://github.com/pengdonglin137/FuncGraph/actions/runs/43565377158)，同一发布提交的 9 个 job 全部成功，包含 publish |
| 本地回归 | `python3 -m unittest discover -s tests -p 'test_*.py' -v`：1,479 项，2 项可选环境检查跳过，其余通过；raw、uftrace 和 Chromium 依赖均要求可用；另设置 `FG_SAMPLE_VMLINUX` 和 `FG_TRACES` 验证真实样例 |
| 跳过与补测 | 标准全量运行未设置 `FG_VMLINUX` / `FG_KERNEL_SRC`，跳过真实内核 BTF / 固定地址 faddr2line 对照；另外单独通过真实镜像 BTF、编译器 clone 检查，以及当前镜像三处有效地址与内核 `scripts/faddr2line` 的对照 |
| 包构建与安装 | universal wheel、sdist、x86_64 native wheel 的构建和 Twine 检查通过；三种包分别在仓库外新环境安装 base / `[all]` 并跑 14 项冒烟测试；可选依赖和未内置 uftrace 的跳过符合包类型，native `[all]` + raw + uftrace 零跳过 |
| 发布附件 | 六个附件全部下载，均与 GitHub 展示的 SHA-256 一致；`SHA256SUMS` 的五项全部匹配；两架构 manifest、ELF 架构、许可证、上游 pin 均核对通过；两份 wheel 内对应源码均与独立源码附件逐字节一致，并核对其内容与本地构建源码一致 |
| 正式包试装 | 实际下载的 x86_64 wheel，仓库外新虚拟环境、`[all]`、`--with-extras --with-raw --with-uftrace`：14 项全部通过、零跳过；ARM64 安装 / 采集验证由对应 runner 完成 |
| 样例完整性 | 内核样例 485 个调用；混合样例 714 个调用（25 user / 689 kernel）；刷新前后除构建时间外的完整模型一致，保留时间、层级、flags、参数、raw 信息、符号与源码数据 |
| Pages | 推标签前七文件与该发布提交逐字节一致；SHA-256 见下表。之后留档文档提交不改变已发布标签或这些站点文件 |
| 诊断与重试 | 最初启用可选真实镜像对照时，测试中三处固定地址范围不适用当前镜像，也不适用旧镜像；未修改断言或把原固定地址测试记为通过，改用当前镜像有效地址另行对照。GitHub API 共享额度耗尽和临时连接超时后等待恢复；未绕过发布门禁或移动标签 |
| 验收时状态 | 正式 latest 为 `v0.9.4`，标签仍指向上述发布提交；后续仅补交验收记录，不再升版本、不重发，不向 PyPI 上传 |

<details>
<summary>v0.9.4 线上 Pages 文件 SHA-256</summary>

| 文件 | SHA-256 |
| --- | --- |
| `.nojekyll` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `index.html` | `edb6e80e8cf486491db9dc7bb731b9eee0f4390131566926a5c13e164e87d539` |
| `sample.html` | `5dcc0877d0b0984f65111d4ce7016b320c810ef0f97401b91912327529b1a3e4` |
| `sample.png` | `651e8d70dea8fc0fe0759ec530b015e07f8c8dd471d233c38d80ba7a8fd6dd5a` |
| `sample_mixed.html` | `8d88a43fe53b359719248683685cf402eb03e0d4a299f1677fe9fcc32b46318f` |
| `sample_mixed.png` | `589bae242db5c3f5967e77c4a668ad51a3b93efc7a6025560b217118e45c196d` |
| `sample_text.png` | `9a7c15ea6fbbed5d8960af7ea7cb156379b90cf7a0ba4c75ddbaa037478b8b73` |

</details>

### 已验证：v0.9.3

| 项目 | 已验证结果 |
| --- | --- |
| 发布时间 | 2026-11-13 14:12:06 UTC；[正式 Release](https://github.com/pengdonglin137/FuncGraph/releases/tag/v0.9.3) |
| 发布提交 | `50d6e311dfe55b7dd855a1bdac9097b147ba7399` |
| Annotated tag 对象 | `v0.9.3` → `23604833e840b92b38df05f0e11bcc62db308aa3`，解析到上述发布提交 |
| CI / Pages | [运行 43433898114](https://github.com/pengdonglin137/FuncGraph/actions/runs/43433898114)，8 个 job 全部成功 |
| Release | [运行 43437931828](https://github.com/pengdonglin137/FuncGraph/actions/runs/43437931828)，9 个 job 全部成功 |
| 本地回归 | `python3 -m unittest discover -s tests -p 'test_*.py' -v`：1,390 项，3 项可选环境检查跳过，其余通过；raw、uftrace 和浏览器依赖均要求可用 |
| 跳过项 | 未设置 `FG_VMLINUX` / `FG_KERNEL_SRC` 的真实内核 BTF / faddr2line 对照，以及未设置 `FG_TRACES` 的额外 trace 检查；不把这三项记为已覆盖 |
| 发布附件 | 六个附件全部下载，`SHA256SUMS` 的五项全部匹配；两份 native manifest 校验通过；x86_64 wheel 内原生源码与单独附件一致 |
| 正式包试装 | 实际下载的 x86_64 wheel，仓库外新虚拟环境、`[all]`、`--with-extras --with-raw --with-uftrace`：14 项全部通过；内置 uftrace 为 v0.20；ARM64 由对应 runner 完成安装 / 采集验证 |
| Pages | 在推标签前完成；`index.html`、两份样例 HTML、三张 PNG、`.nojekyll` 共七文件与部署提交逐字节一致，并记录 SHA-256 |
| 验收时状态 | `master` 与 `v0.9.3` 均指向发布提交，正式 latest 为 `v0.9.3`；未移动旧标签，未向 PyPI 上传 |

### 历史记录：v0.9.1

以下是历史记录，**不要重新执行该版本的建标签操作**。

| 项目 | 已验证结果 |
| --- | --- |
| 发布时间 | 2026-11-09 15:01:03 UTC |
| 标签 | `v0.9.1` |
| 提交 | `40283ac9e7cdfca985ab21798df1e19d51c3e755`（`release v0.9.1`） |
| CI | 运行 `42639944966`，成功 |
| Release | 运行 `42639944990`，成功 |
| 发布附件 | `funcgraph-0.9.1-py3-none-any.whl`、`funcgraph-0.9.1.tar.gz`、`SHA256SUMS` |
| 发布后验证 | 已下载附件，核对校验值、wheel 版本和内置 JavaScript 资源 |

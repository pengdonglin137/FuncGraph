# FuncGraph 打包与发布操作清单

[English](releasing.md)

本文记录可重复使用的发布流程，以及 2026 年 11 月 9 日完成的 `v0.9.1` 发布。
当前自动化发布到 **GitHub Releases，不是 PyPI**，无需配置 PyPI token。

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

先确认工作区干净；有未提交工作时先提交或妥善保存，不要直接切分支。
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

以下以 **`0.9.2` 为下一次发布的示例**，不是要求每次照抄同一个版本。
`v0.9.1` 已经发布，不能重新创建或移动到新提交。

```bash
next_version=0.9.2
release_tag="v${next_version}"
git tag --list "$release_tag"
git ls-remote --tags origin "refs/tags/$release_tag" "refs/tags/$release_tag^{}"
```

两个标签查询都应无输出；已有该标签时，请选择新的版本。
修改 `fglib/constants.py` 的 `APP_VERSION`，使其等于 `next_version`，并按需同步
`README.md`、`README.cn.md` 的版本/安装示例及 `CLAUDE.md` 的项目版本。
不要在 `pyproject.toml` 中另外维护一份固定版本。

支持 `X.Y.Z`、`X.Y.ZaN`、`X.Y.ZbN`、`X.Y.ZrcN`。
标签必须严格等于 `v` + `APP_VERSION`：例如版本 `0.9.2` 对应 `v0.9.2`，
版本 `0.10.0rc1` 对应 `v0.10.0rc1`。预发布版本自动标为 prerelease，且不标为 latest。

## 3. 本地测试与打包

使用临时虚拟环境，避免修改系统 Python；输出到全新目录，避免混入旧的 `dist/` 包。

```bash
test "$(python3 -c 'from fglib.constants import APP_VERSION; print(APP_VERSION)')" = "$next_version"
build_dir="$(mktemp -d)"
python3 -m venv "$build_dir/venv"
"$build_dir/venv/bin/python" -m pip install build twine pyelftools pygments
PATH="$build_dir/venv/bin:$PATH" tests/run.sh
"$build_dir/venv/bin/python" -m build --outdir "$build_dir/dist"
"$build_dir/venv/bin/python" -m twine check "$build_dir/dist/funcgraph-${next_version}-py3-none-any.whl" "$build_dir/dist/funcgraph-${next_version}.tar.gz"
git diff --check
```

检查测试中的 skip 原因：本地缺少浏览器工具导致跳过，不代表完成了浏览器验证。
远端会在 Python 3.8、3.12 上测试和构建，3.12 还执行 Chromium 浏览器测试。
wheel 和源码包均经过 Twine 检查，并在仓库外的独立虚拟环境中分别验证基础安装
和 `[all]` 安装，覆盖命令入口、资源文件、报告/时间轴/对比、JSON 导出及 ELF 地址解析。
Python 3.12 的包和校验文件保存为 Actions artifact：`python-dist`。

## 4. 提交版本、创建标签并推送

先审查全部 diff。以下仅暂存版本及发布文档；功能改动应在此前提交。
若本次还修改了其他待发布文件，应审查后单独加入，避免直接 `git add .` 混入无关内容。

```bash
git diff
git add fglib/constants.py README.md README.cn.md CLAUDE.md docs/releasing.md docs/releasing.cn.md
git commit -m "release $release_tag"
git status --short
test "$(python3 -c 'from fglib.constants import APP_VERSION; print(APP_VERSION)')" = "$next_version"
git tag -a "$release_tag" -m "FuncGraph $next_version"
git push --atomic origin master "refs/tags/$release_tag"
```

创建标签前，`git status --short` 应无输出。`--atomic` 保证远端分支和标签要么一起
更新，要么都不更新。如果版本提交已经 push 到 `master`，只需推标签：

```bash
git push origin "refs/tags/$release_tag"
```

发布工作流先比较**标签指向的提交**中的版本，再运行共用测试/打包，最后下载
`python-dist`、核对 `SHA256SUMS`，创建带自动生成说明的 Release。任何前置 job
失败都不会发布。`master` CI（含 Pages）和标签 Release 是两次独立运行，需分别检查。

## 5. 确认真正发布成功

在 GitHub Actions 中找到本次提交对应的 `ci` 和标签对应的 `release`，等待两者成功。
也可以用 GitHub CLI 查看近期运行：

```bash
gh run list --workflow ci.yml --branch master --limit 5
gh run list --workflow release.yml --limit 5
```

找到正确的运行 ID 后，执行 `gh run watch RUN_ID --exit-status` 等待结束；
失败时用 `gh run view RUN_ID --log-failed` 查看日志。`RUN_ID` 需替换为实际值，
不要仅凭最近一次其他提交的成功记录判断本次发布。

再检查 Releases 页面：标签正确、已发布而非 draft、正式版/预发布状态正确，
并且有以下三个附件：

- `funcgraph-<版本>-py3-none-any.whl`：Python wheel。
- `funcgraph-<版本>.tar.gz`：源码发布包，不是 GitHub 自动附加的源码快照。
- `SHA256SUMS`：上面两个包的 SHA-256 校验值。

下载真正的 Release 附件，再校验和试装，而不只是检查本地构建结果：

```bash
gh release view "$release_tag"
download_dir="$(mktemp -d)"
gh release download "$release_tag" --dir "$download_dir"
(cd "$download_dir" && sha256sum --check SHA256SUMS)
python3 -m venv "$download_dir/venv"
"$download_dir/venv/bin/python" -m pip install "$download_dir/funcgraph-${next_version}-py3-none-any.whl[all]"
(cd "$download_dir" && ./venv/bin/python -I -c 'from importlib.metadata import version; print(version("funcgraph"))')
"$download_dir/venv/bin/funcgraph" --help
"$download_dir/venv/bin/fgreport" --help
```

应看到两个校验都通过，安装后显示的版本等于 `next_version`。
完整复验下载包可运行仓库中的已安装包测试，`-I` 防止误导入源码目录：

```bash
"$download_dir/venv/bin/python" -I "$PWD/tests/smoke_installed.py" --with-extras
```

这组测试还需要 C 编译器和 binutils。源码包可在另一个全新虚拟环境中，用下载的
`.tar.gz` 替代 wheel 安装验证。最终记录版本、提交、Actions 运行 ID 和附件校验结果。

### 用户安装与使用

安装方式是下载 Release 附件后通过本地路径执行 `pip install`，如上述命令；
不是直接从 PyPI 执行 `pip install funcgraph`。
基础包没有 Python 运行时依赖；`[fast]` 安装 pyelftools，`[highlight]` 安装 Pygments，
`[all]` 安装两者。包提供 `funcgraph`、`fgreport`、`fastfaddr2line` 和 `fgcapture.sh`。
快速地址解析仍需相应架构的系统 `addr2line`；抓取脚本需 Linux、Bash、root 和 tracefs。
源码运行方式 `python3 funcgraph.py`、`./fgreport` 保持可用。
两个发布包都包含解析器和 CSS/JavaScript；仓库资源在 `assets/`，wheel 内在 `fglib/_assets/`。

## 6. 常见问题与处理

| 现象 | 原因与处理 |
| --- | --- |
| push `timeline` 后没有 CI | 没有 PR 时属预期；创建 PR 或手动运行 `ci`，不能把“没有运行”当作“通过”。 |
| `master` CI 通过，但没有 Release | 普通分支 push 不发布；检查匹配标签是否已推送到 `origin`，以及标签的 `release` 运行状态。 |
| `check-tag` 失败 | 检查标签提交里的 `APP_VERSION` 是否与标签严格一致，以及版本格式是否支持。 |
| 测试、构建或资源检查失败 | 查看具体 job 日志；源码目录能运行不代表安装包包含了所有文件。 |
| artifact、校验或发布权限出错 | 检查 `build`/`publish` 日志、`python-dist` 和仓库/组织 Actions 策略；发布 job 需要 `contents: write`。 |
| runner、网络或服务临时失败 | 确认尚未创建 Release 后，在 Actions 选择 Re-run failed jobs 或 Re-run all jobs。 |
| 必须修改源码或工作流才能修复 | 重跑仍使用原标签提交，不会使用后来修改的 `master`；提交修复、选择新版本和标签，重新发布。 |
| 标签或 Release 已存在 | 不覆盖已发布附件，不移动已发布标签；先核实旧版本，修复以新版本发布。 |
| `--atomic` push 被拒绝 | 远端分支和标签均未更新；先检查远端新提交和权限，不要强推或偷偷移动标签。 |

## 7. 已完成的发布记录：v0.9.1

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

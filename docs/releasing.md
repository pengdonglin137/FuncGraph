# Building and releasing FuncGraph

[中文发布操作清单](releasing.cn.md)

## What triggers a release?

This automation publishes **GitHub Releases, not PyPI**. A successful branch push
is not a release: a matching version tag must also be pushed, and its release
workflow must finish successfully.

| Event | Result |
| --- | --- |
| Push to `master` | CI tests and packages; deploys GitHub Pages after checks pass |
| Pull request or manual `ci` run | CI tests and packages; no Release or Pages deployment |
| Push to `timeline` or another feature branch only | No workflow triggered by that push |
| Push a `v*` tag | Checks the version, tests and packages, then publishes a Release |

`.github/workflows/ci.yml` and `.github/workflows/release.yml` share
`.github/workflows/test-and-package.yml`. Only the release publishing job has
`contents: write`. GitHub Actions must be enabled; publication uses the automatic
`GITHUB_TOKEN`, so no personal access token or PyPI token needs to be configured.
Push the tag with your normal authenticated Git connection, not another
workflow's default `GITHUB_TOKEN`, which does not trigger new workflow runs.

## Release target: v0.9.3

The November 13, 2026 release target is `0.9.3`: unified capture, large-Text
rendering and navigation improvements, Timeline fixes and redesigned bilingual
documentation are listed in [the release notes](releases/v0.9.3.md). The
publish job includes a matching `docs/releases/vTAG.md` file in the Release body
when present, followed by generated GitHub notes. A version change alone does
not publish anything: verify the actual tag, workflow and attachments below.
Keep the published `v0.9.2` tag and earlier verified release records as history.

## Release checklist

Run commands from the repository root in the same Bash session. **Stop if any
command fails.** `0.9.3` is the current release example, not a version to reuse
unconditionally. Published tags such as `v0.9.2` must not be moved or overwritten.

### 1. Prepare the release commit

Requirements: Python 3.8+, Git with permission to push to the repository, and
`addr2line` from binutils. Node.js and ShellCheck enable additional local checks;
Playwright with Chromium enables browser tests. CI supplies these tools. The
optional GitHub CLI (`gh`) must be installed and authenticated to use the status
and download commands below; otherwise use the repository's Actions and Releases
pages and download the same attachments in the browser.

Start with a clean working tree; commit or set aside existing work before switching
branches. Merge the intended changes (for example from `timeline`) into `master`
before releasing. Do not resolve unexpected divergence with a reset or force push.

```bash
git status --short
git fetch origin --tags
git switch master
git merge --ff-only origin/master
git log -5 --oneline
next_version=0.9.3
release_tag="v${next_version}"
git tag --list "$release_tag"
git ls-remote --tags origin "refs/tags/$release_tag" "refs/tags/$release_tag^{}"
```

Both tag queries must produce no output; otherwise choose an unused version.
Edit `APP_VERSION` in `fglib/constants.py` to match `next_version`, and update the
version and installation examples in `README.md`, `README.cn.md`, and `CLAUDE.md`
as needed. `pyproject.toml` reads this single version source automatically.

Supported versions are `X.Y.Z`, `X.Y.ZaN`, `X.Y.ZbN`, and `X.Y.ZrcN`. The tag must
be exactly `v` plus `APP_VERSION`, such as `v0.9.2` or `v0.10.0rc1`. The latter
creates a prerelease and is not marked as the latest release.

### 2. Validate locally

Before packaging, verify the dependency lock with
`python3 third_party/check_pins.py --allow-uninitialized`. This checks all indexed
submodule commits, even for Python-only builds; initialized checkouts must also
match and have no tracked edits. Native build scripts additionally require their
selected submodules to be initialized. See [dependency pins](../third_party/README.md).

Use a temporary virtual environment and a fresh output directory to avoid global
Python changes or accidentally checking a stale package:

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

Review skipped tests: missing browser tools can cause local skips, not full browser
coverage. CI tests on Python 3.8 and 3.12, including Chromium on 3.12. It builds
both distributions, runs Twine checks, and installs each into fresh virtual
environments outside the checkout, without extras and then with `[all]`.
Installed-package smoke tests cover command entry points, embedded resources,
reports/timeline/comparison, JSON export, and a compiled ELF resolver fixture.
The Python 3.12 packages and checksums become the `python-dist` Actions artifact.

The shared workflow also builds native **Linux x86_64 and aarch64** wheels on
Ubuntu 22.04 runners, tests the patched exporter, then moves the original build
prefix away before installing the wheel outside the checkout and recording a
fresh workload. Each platform produces `native-linux_ARCH-dist`. These wheels
include uftrace and all five libmcount variants, not just a download link. They
use `py3-none-linux_ARCH`, not manylinux tags; glibc and OS libraries remain
system dependencies. The dependency lock freezes repository commits, not apt
package versions.

To reproduce the native build on a supported Linux host, after the Python-only
checks above (the build script refuses existing native output directories):

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

Run the native verification on each actual target architecture; an x86_64 test
does not establish ARM64 support. `FG_UFTRACE_RELEASE=1` disables optional
Python/Lua, curses, unwind and Capstone features for the release build.
ARM64 release builds must report `patchable` in their version and pass the
`aarch64_patchable` native unit tests. Export/installation tests use
`-fpatchable-function-entry=2` with `record -P .`, not ARM64 `-pg` with its
clobbered first argument; never bypass the argument assertions to release.
Use `--with-extras` to activate the extra-dependency assertions; installing
`[all]` alone does not enable them. Fast source-snippet checks must pass without
`gawk` too, rather than skipping the source-content assertion.
The native wheel contains GPL-2.0 `COPYING`, executable/library hashes, the fixed
uftrace base and patch hashes, and `fglib/_native/uftrace/source.tar.gz`: the full
corresponding source with the maintained patches already applied, plus build
instructions. CI attaches the same source archive separately. Do not substitute
GitHub's automatic FuncGraph source snapshot for this native corresponding source.

### 3. Commit, tag, and push

Review all pending changes before staging. The following stages the version and
release documentation only; any implementation changes must already be committed.

```bash
git diff
git add fglib/constants.py README.md README.cn.md CLAUDE.md docs/releasing.md docs/releasing.cn.md
git commit -m "release $release_tag"
git status --short
test "$(python3 -c 'from fglib.constants import APP_VERSION; print(APP_VERSION)')" = "$next_version"
git tag -a "$release_tag" -m "FuncGraph $next_version"
git push --atomic origin master "refs/tags/$release_tag"
```

The status must be clean before tagging. The atomic push updates `master` and
the tag together, or neither. If `master` was already pushed, just push the tag:

```bash
git push origin "refs/tags/$release_tag"
```

No force push is required.

The tag workflow runs `check-tag` → `build` (shared tests and packaging) →
`publish` (checksum verification and Release creation with generated notes).
It does not publish if an earlier release job fails. The branch CI, including
Pages, is a separate run; check both workflows rather than assuming one proves
the other passed.

### 4. Verify the published result

Open Actions and select the runs for the release commit and tag. With `gh`:

```bash
gh run list --workflow ci.yml --branch master --limit 5
gh run list --workflow release.yml --limit 5
```

For each relevant run, use `gh run watch RUN_ID --exit-status` (replace `RUN_ID`
with the actual ID). Inspect `gh run view RUN_ID --log-failed` on failure.
Then confirm the Release is published, not a draft, has the intended stable or
prerelease status, and contains all six intended attachments:

- `funcgraph-VERSION-py3-none-linux_x86_64.whl`: bundled recorder for Linux x86_64.
- `funcgraph-VERSION-py3-none-linux_aarch64.whl`: bundled recorder for Linux ARM64.
- `funcgraph-VERSION-py3-none-any.whl`: Python-only wheel.
- `funcgraph-VERSION.tar.gz`: Python source distribution.
- `funcgraph-uftrace-VERSION-source.tar.gz`: fully patched native corresponding source.
- `SHA256SUMS`: checksums for all five distributions/source archives.

```bash
gh release view "$release_tag"
download_dir="$(mktemp -d)"
gh release download "$release_tag" --dir "$download_dir"
(cd "$download_dir" && sha256sum --check SHA256SUMS)
python3 -m venv "$download_dir/venv"
"$download_dir/venv/bin/python" -m pip install "$download_dir/funcgraph-${next_version}-py3-none-linux_$(uname -m).whl[all]"
(cd "$download_dir" && ./venv/bin/python -I -c 'from importlib.metadata import version; print(version("funcgraph"))')
"$download_dir/venv/bin/funcgraph" --help
"$download_dir/venv/bin/fgreport" --help
"$download_dir/venv/bin/fguftrace" --version
```

The printed version must match `next_version`, and all checksums must pass.
For full installed-package validation of the downloaded wheel, without importing
the source checkout:

```bash
"$download_dir/venv/bin/python" -I "$PWD/tests/smoke_installed.py" --with-extras --with-uftrace
```

This smoke test also needs a C compiler and binutils. Source installations can be
checked in another fresh environment using `funcgraph-VERSION.tar.gz` instead of
the native wheel; omit `--with-uftrace` for that Python-only package. Test the
universal wheel separately too. Record the tag, commit, run IDs, and verification outcome before
declaring the release complete.

## Install a release package

Download the wheel or source distribution and install it by local path, as in the
verification commands above; `pip install funcgraph` is not part of this workflow.
The base package has no Python runtime dependencies. Extras `fast`, `highlight`,
and `all` install pyelftools, Pygments, or both. Installed commands are `funcgraph`,
`fgreport`, `fgview`, `fgcapture`, `fguftrace`, `fastfaddr2line`, and `fgcapture.sh`. Native wheels include
the patched recorder and recording libraries; on Ubuntu, the recorder needs
`libtraceevent1`, `libelf1`, `libdw1`, `liblzma5` and `libstdc++6` in addition to
compatible glibc. Python-only packages fall back to patched uftrace on `PATH`,
or `--uftrace-bin PATH`. Saved JSONL reports need no native recorder.
Fast resolution still requires
the appropriate system `addr2line`. Kernel-only capture needs Linux, Bash, root
and tracefs; `fgcapture --user` needs no root / tracefs, while mixed mode requires
root and idle dedicated tracefs. Source-based commands (`python3 funcgraph.py`, `./fgreport`) remain supported.
Both distributions include the resolver and CSS/JavaScript resources; source
assets live in `assets/`, while wheels install them under `fglib/_assets/`.

| Release attachment | Bundled uftrace | Intended use |
| --- | --- | --- |
| `*-linux_x86_64.whl` | Executable and five libmcount libraries | x86_64 Linux user/mixed recording |
| `*-linux_aarch64.whl` | Executable and five libmcount libraries | ARM64 Linux user/mixed recording |
| `*-py3-none-any.whl`, Python sdist | None | Offline exported-record reports, or separately supplied patched uftrace |
| `funcgraph-uftrace-*-source.tar.gz` | Corresponding source, not an executable installation | Audit / rebuild native components |

Check `fguftrace --version` after installation. See the [unified capture cases](capture.md)
for building, recording, reporting and preserving artifacts, or [pigz + zlib](pigz.md)
for an application and instrumented dependency.

## Troubleshooting

Sample updates follow branch CI, not the release-tag workflow. Pages deploys only after **all** shared checks pass, including the native raw build and package jobs; successful Python test jobs alone do not prove deployment. If a raw build fails, Pages is skipped and keeps serving the last successfully deployed sample and screenshots. Inspect the failed job, push a fix when source changes are required, and verify the new push's `pages` job and online files. Re-running an old failed commit cannot pick up a later fix; a manual `workflow_dispatch` CI run does not deploy Pages. Compare the online `sample.html`, `sample.png`, `sample_text.png`, `sample_mixed.html`, and `sample_mixed.png` with the files at the deployed commit, rather than treating a browser refresh as deployment verification.

| Symptom | Check / action |
| --- | --- |
| Feature branch push has no Actions run | Expected without a PR. Open a PR or manually run `ci`; only `master` branch pushes trigger CI automatically. |
| `master` CI passed, but no Release | Branch pushes do not release. Check that the matching tag exists on `origin` and inspect its `release` run. |
| `check-tag` rejects a version | Compare the tag with `APP_VERSION` in the **tagged commit**, not the current branch. |
| Test, packaging, or resource smoke test failed | Inspect the failed job; repository imports working locally do not prove installed resources are included. |
| Artifact/checksum failure or publication permission denied | Inspect `build`/`publish` logs, `python-dist` and both `native-linux_ARCH-dist` artifacts, and repository/organization Actions policies; the publish job needs `contents: write`. |
| Recorder cannot load a shared library, or a wheel is not supported | Install the documented OS runtime libraries; choose the correct architecture and a compatible Linux/glibc host. Do not rename a platform wheel as universal or manylinux. |
| Temporary runner, network, or service failure | After confirming that no Release was created, use Actions → Re-run failed jobs (or Re-run all jobs). |
| A source/workflow fix is needed | A rerun still uses the original tagged commit. Commit the fix, choose a new version/tag, and repeat; a push to `master` alone does not fix the old tag. |
| Tag or Release already exists | Do not overwrite published assets or move published tags. Check the existing result and use a new version for corrections. |
| Atomic push rejected | Neither remote ref was updated. Check remote changes and permissions before retrying; do not force push or silently move the tag. |

## Verified release record: v0.9.1

This is a historical record, **not a tag to recreate**.

| Item | Verified result |
| --- | --- |
| Publication time | November 9, 2026, 15:01:03 UTC |
| Tag / commit | `v0.9.1` / `40283ac9e7cdfca985ab21798df1e19d51c3e755` |
| Actions | `ci`: `42639944966`; `release`: `42639944990`; both succeeded |
| Attachments | `funcgraph-0.9.1-py3-none-any.whl`, `funcgraph-0.9.1.tar.gz`, `SHA256SUMS` |
| Verification | Downloaded published attachments; checksums, wheel version, and embedded JavaScript resources verified |

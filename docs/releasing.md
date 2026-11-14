# Building and releasing FuncGraph

[中文发布操作清单](releasing.cn.md)

## Required gates for every release

- [ ] The user explicitly requested publication; choose an unused version and never move a published tag.
- [ ] Review the intended changes and working tree; preserve unrelated files without force pushes or destructive cleanup.
- [ ] Update the single `APP_VERSION` source, `docs/releases/vTAG.md`, and affected usage documentation.
- [ ] Refresh both sample HTML files, version badges and screenshots without losing recorded data or matching source/kernel revisions.
- [ ] Check dependency pins and patches; pass local tests, builds and isolated installations, recording every skip reason.
- [ ] Stage explicit paths and write a detailed commit message; push `master` first and record its release commit SHA.
- [ ] Wait for all CI checks and the `pages` job for that SHA, then compare every deployed Pages file.
- [ ] Create an annotated tag for that verified commit only, and push the tag separately.
- [ ] Wait for the complete tag workflow; verify version, draft/prerelease/latest state and all six attachments.
- [ ] Download the published attachments; check SHA-256, both native payload manifests and corresponding source, then install in isolation.
- [ ] Record time, tag, commit, run IDs, tests/skips, attachments and Pages verification before declaring completion.

**Default order: prepare → local validation → push master → accept CI / Pages → push tag → accept Release → record evidence.**
Stop at a failed gate. A triggered run, some successful jobs or a browser refresh is not a completed release.

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

## Most recently completed release: v0.9.4

`v0.9.4` was published on November 14, 2026: saved analysis views, staged exact
filters, inline-frame presentation, consistent detail titles and safe BTF
compiler-clone arguments are listed in [the release notes](releases/v0.9.4.md). The
publish job includes a matching `docs/releases/vTAG.md` file in the Release body
when present, followed by generated GitHub notes. A version change alone does
not publish anything: verify the actual tag, workflow and attachments below.
Keep the published `v0.9.4`, `v0.9.3`, `v0.9.2` tags and earlier verification records as history; do not reuse them.

## Release checklist

Run commands from the repository root in the same Bash session. **Stop if any
command fails.** `0.9.5` below is an example for a future release, not an upgrade
or publication of that version. Always check availability; published tags such as `v0.9.4` must not be moved or overwritten.

### 1. Prepare the release commit

Requirements: Python 3.8+, Git with permission to push to the repository, and
`addr2line` from binutils. Node.js and ShellCheck enable additional local checks;
Playwright with Chromium enables browser tests. CI supplies these tools. The
optional GitHub CLI (`gh`) must be installed and authenticated to use the status
and download commands below; otherwise use the repository's Actions and Releases
pages and download the same attachments in the browser.

Identify all intended changes; commit or set aside existing work before switching
branches. Preserve unrelated untracked files and exclude them from commits; do not
use `git clean`, a reset or a force push to obtain a "clean" tree.
Merge the intended changes (for example from `timeline`) into `master`
before releasing. Do not resolve unexpected divergence with a reset or force push.

```bash
git status --short
git fetch origin --tags
git switch master
git merge --ff-only origin/master
git log -5 --oneline
```

Choose an unused version; the following future-version example must not be copied blindly:

```bash
next_version=0.9.5
release_tag="v${next_version}"
git tag --list "$release_tag"
git ls-remote --tags origin "refs/tags/$release_tag" "refs/tags/$release_tag^{}"
```

Both tag queries must produce no output; otherwise choose an unused version.
Edit `APP_VERSION` in `fglib/constants.py` to match `next_version`, and update the
version and installation examples in `README.md`, `README.cn.md`, and `CLAUDE.md`
as needed. `pyproject.toml` reads this single version source automatically.
Create `docs/releases/$release_tag.md` describing changes, compatibility,
dependencies and validation scope. Use complete, tag-pinned GitHub URLs for
repository documents in the Release body; relative links may not work there.

Supported versions are `X.Y.Z`, `X.Y.ZaN`, `X.Y.ZbN`, and `X.Y.ZrcN`. The tag must
be exactly `v` plus `APP_VERSION`, such as `v0.9.2` or `v0.10.0rc1`. The latter
creates a prerelease and is not marked as the latest release.

#### Refresh samples and screenshots

When output or UI changes, regenerate both reports from `samples/sample_io.txt`
and `samples/sample_mixed.jsonl`. See [usage cases](examples.md) for offline replay
and the [uftrace guide](uftrace.md) for the source-enriched mixed recipe. Preserve
matching kernel images, user sources, BTF/DWARF and pinned source-browser revisions;
unenriched offline replay must not replace an existing complete sample.
Check both HTML titles and version badges, then refresh the images:

```bash
python3 samples/make_shots.py
python3 docs/img/make_shots.py tl_detail
```

Review `sample.html`, `sample_mixed.html`, `sample.png`, `sample_mixed.png`,
`samples/sample_text.png` and affected `docs/img/*.png`. Updating only badges
does not deliver a new viewer; never trim the recording or discard arguments,
caller sites or source evidence just to update a demo.

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

### 3. Accept master and Pages before pushing a tag

Review all pending changes before staging. The following stages the version and
release documentation only; any implementation changes must already be committed.
Explicitly stage reviewed sample/image changes before committing. No tracked
changes should remain accidentally omitted after the release commit.

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

Stage reviewed sample/image changes separately. The commit message must describe
the version, purpose, key changes, test commands/results, skips and sample updates,
not just "update". Do not include unrelated untracked files.

Select the branch CI whose `head_sha` equals `release_commit` in Actions. With `gh`:

```bash
gh run list --workflow ci.yml --branch master --limit 5
```

Use `gh run watch RUN_ID --exit-status` with the actual ID, or wait in the browser.
Python 3.8 / 3.12, raw, both native architectures, both Python package jobs and
`pages` must all succeed. A manual CI run does not deploy Pages, and another
commit's successful run cannot satisfy this gate.

#### Accept the deployed Pages files

Stage the site from this commit and compare all seven online files. This needs
`curl` and SHA-256 tools. Online `sample_text.png` is at the site root, not the
repository's `samples/sample_text.png` path.

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

On a mismatch, check the deployment commit and caching, wait for propagation and
verify again. Do not move tags or trim samples to bypass this gate. Retain the
comparison results and hashes. Only then tag the commit just verified:

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

Do not use a combined `--atomic` branch/tag push as the default: it bypasses
pre-tag CI / Pages acceptance. No force push is required.

The tag workflow runs `check-tag` → `build` (shared tests and packaging) →
`publish` (checksum verification and Release creation with generated notes).
It does not publish if an earlier release job fails. The branch CI, including
Pages, is a separate run; check both workflows rather than assuming one proves
the other passed.

### 4. Verify the published result

Open Actions and select the runs for the release commit and tag. With `gh`:

```bash
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

GitHub's two automatic Source code snapshots do not count toward these six
attachments and cannot replace the native corresponding source. Verify that a
stable release becomes the intended latest; a prerelease must not replace it.

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

The printed version must match `next_version`, and all checksums must pass.
For full installed-package validation of the downloaded wheel, without importing
the source checkout:

```bash
(cd "$download_dir" && env -u FG_UFTRACE -u FG_UFTRACE_PREFIX ./venv/bin/python -I "$workspace/tests/smoke_installed.py" --with-extras --with-uftrace)
```

This smoke test also needs a C compiler and binutils. Source installations can be
checked in another fresh environment using `funcgraph-VERSION.tar.gz` instead of
the native wheel; omit `--with-uftrace` for that Python-only package. Test the
universal wheel separately too. Record the tag, commit, run IDs, and verification outcome before
declaring the release complete.
Add `--with-raw` when the native decoder dependencies are available; record
unexecuted optional checks as uncovered. Check both platform wheels' native
manifests, pinned upstream and file hashes. The x86_64 wheel's
`fglib/_native/uftrace/source.tar.gz` must exactly match the separate source attachment.

`gh` is not required to publish: normal Git SSH pushes trigger the workflows.
Use Actions / Releases in the browser and download the six attachments to a fresh
`download_dir`, then continue the checksum and installation checks. Public REST
endpoints `actions/runs?head_sha=COMMIT`, `actions/runs/RUN_ID/jobs`,
`releases/tags/TAG` and `releases/latest` also expose the relevant results without
a PAT. On 403 / 429 rate limits, respect `X-RateLimit-Reset` / `Retry-After` or use
the browser; do not poll aggressively or mistake a rate limit for a failed build.

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
| Native prefix pin / patch checksums mismatch | Do not reuse stale caches or bypass argument assertions. Rebuild with the current pinned dependencies and patches. |

## Record the release acceptance

After publishing, add the following evidence to both language versions. A later
documentation commit may record the outcome; never move the published tag to
include that record. Recording it does not require another version or release.

```text
Version / UTC publication time:
Tag object / release commit SHA:
CI run ID / head_sha / all jobs and Pages outcome:
Release run ID / head_sha / all jobs and publish outcome:
Local test commands, counts and every skip reason:
Six attachments, SHA256SUMS, native manifests / corresponding source:
Downloaded-package isolated install: architecture, extras, raw, uftrace, tests:
Pages deployment commit, seven-file comparisons and hashes:
Uncovered cases, failures and retry reasons, if any:
```

### Verified: v0.9.4

| Item | Verified result |
| --- | --- |
| Publication | November 14, 2026, 06:41:47 UTC; [formal Release](https://github.com/pengdonglin137/FuncGraph/releases/tag/v0.9.4) |
| Release commit | `e05e9280f1f072c1cbe1fd6689f295def7acce3e` |
| Annotated tag object | `v0.9.4` → `a886cf2c4e0ae214e60776c72bfa533fa51d29db`, resolving to that release commit |
| CI / Pages | [Run 43563486436](https://github.com/pengdonglin137/FuncGraph/actions/runs/43563486436); all 8 jobs for the release commit succeeded, including Pages deployment |
| Release | [Run 43565377158](https://github.com/pengdonglin137/FuncGraph/actions/runs/43565377158); all 9 jobs for the same release commit succeeded, including publish |
| Local regression | `python3 -m unittest discover -s tests -p 'test_*.py' -v`: 1,479 tests, 2 optional-environment skips, all others passed; raw, uftrace and Chromium dependencies were required; `FG_SAMPLE_VMLINUX` and `FG_TRACES` enabled real-sample checks |
| Skips and separate checks | The standard full run omitted `FG_VMLINUX` / `FG_KERNEL_SRC`, skipping real-kernel BTF / fixed-address faddr2line comparisons; separate real-image BTF, compiler-clone checks and three valid current-image addresses compared against kernel `scripts/faddr2line` all passed |
| Package builds and installs | Universal wheel, sdist and x86_64 native wheel built and passed Twine; each was installed base / `[all]` into fresh environments outside the checkout and ran 14 smoke tests; optional-dependency / unbundled-uftrace skips matched each package type; native `[all]` + raw + uftrace had no skips |
| Attachments | All six downloaded and matched GitHub's SHA-256 digests; all five `SHA256SUMS` entries matched; both native manifests, ELF architectures, licenses and upstream pins verified; both wheels' corresponding-source archives matched the standalone attachment byte-for-byte, with contents also verified against the local source build |
| Published-package install | Downloaded x86_64 wheel, fresh environment outside checkout, `[all]`, `--with-extras --with-raw --with-uftrace`: all 14 tests passed without skips; ARM64 installation / recording validated on its runner |
| Sample integrity | Kernel sample: 485 calls; mixed sample: 714 calls (25 user / 689 kernel); complete before/after models matched except build time, preserving timings, hierarchy, flags, arguments, raw evidence, symbols and source data |
| Pages | All seven files matched the release commit byte-for-byte before tagging; SHA-256 values follow. Later acceptance-documentation commits do not change the published tag or these site files |
| Diagnostics and retries | Three fixed address ranges in the optional real-image comparison did not fit either the current or older image; assertions were not changed and those original fixed-address tests are not claimed to pass; valid current-image addresses were compared separately. Waited out the shared GitHub API quota and temporary connection timeouts without bypassing gates or moving tags |
| State at acceptance | Stable latest was `v0.9.4`, still pointing at the release commit; the later acceptance record is documentation-only, without another version, republishing or a PyPI upload |

<details>
<summary>v0.9.4 deployed Pages SHA-256</summary>

| File | SHA-256 |
| --- | --- |
| `.nojekyll` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `index.html` | `edb6e80e8cf486491db9dc7bb731b9eee0f4390131566926a5c13e164e87d539` |
| `sample.html` | `5dcc0877d0b0984f65111d4ce7016b320c810ef0f97401b91912327529b1a3e4` |
| `sample.png` | `651e8d70dea8fc0fe0759ec530b015e07f8c8dd471d233c38d80ba7a8fd6dd5a` |
| `sample_mixed.html` | `8d88a43fe53b359719248683685cf402eb03e0d4a299f1677fe9fcc32b46318f` |
| `sample_mixed.png` | `589bae242db5c3f5967e77c4a668ad51a3b93efc7a6025560b217118e45c196d` |
| `sample_text.png` | `9a7c15ea6fbbed5d8960af7ea7cb156379b90cf7a0ba4c75ddbaa037478b8b73` |

</details>

### Verified: v0.9.3

| Item | Verified result |
| --- | --- |
| Publication | November 13, 2026, 14:12:06 UTC; [formal Release](https://github.com/pengdonglin137/FuncGraph/releases/tag/v0.9.3) |
| Release commit | `50d6e311dfe55b7dd855a1bdac9097b147ba7399` |
| Annotated tag object | `v0.9.3` → `23604833e840b92b38df05f0e11bcc62db308aa3`, resolving to that release commit |
| CI / Pages | [Run 43433898114](https://github.com/pengdonglin137/FuncGraph/actions/runs/43433898114); all 8 jobs succeeded |
| Release | [Run 43437931828](https://github.com/pengdonglin137/FuncGraph/actions/runs/43437931828); all 9 jobs succeeded |
| Local regression | `python3 -m unittest discover -s tests -p 'test_*.py' -v`: 1,390 tests, 3 optional-environment skips, all others passed; raw, uftrace and browser dependencies were required |
| Skips | Real-kernel BTF / faddr2line comparisons without `FG_VMLINUX` / `FG_KERNEL_SRC`, and additional traces without `FG_TRACES`; these three checks are not claimed as covered |
| Attachments | All six downloaded; all five `SHA256SUMS` entries matched; both native manifests verified; x86_64 wheel corresponding source matched the separate attachment |
| Published-package install | Downloaded x86_64 wheel, fresh environment outside checkout, `[all]`, `--with-extras --with-raw --with-uftrace`: all 14 tests passed; bundled uftrace v0.20; ARM64 installation / recording validated on its runner |
| Pages | Accepted before tagging; `index.html`, both sample HTML files, three PNGs and `.nojekyll` matched the deployment commit byte-for-byte, with SHA-256 recorded |
| State at acceptance | `master` and `v0.9.3` resolved to the release commit and stable latest was `v0.9.3`; no published tag moved and nothing uploaded to PyPI |

### Historical record: v0.9.1

This is a historical record, **not a tag to recreate**.

| Item | Verified result |
| --- | --- |
| Publication time | November 9, 2026, 15:01:03 UTC |
| Tag / commit | `v0.9.1` / `40283ac9e7cdfca985ab21798df1e19d51c3e755` |
| Actions | `ci`: `42639944966`; `release`: `42639944990`; both succeeded |
| Attachments | `funcgraph-0.9.1-py3-none-any.whl`, `funcgraph-0.9.1.tar.gz`, `SHA256SUMS` |
| Verification | Downloaded published attachments; checksums, wheel version, and embedded JavaScript resources verified |

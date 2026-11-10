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

## Prepared Source Version: v0.9.2

The source version was updated to `0.9.2` on November 10, 2026, together with the sample report and screenshots. This is release preparation only: no `v0.9.2` tag or GitHub Release is created by a version change. Follow the checklist below, verify tag availability again, and check the actual workflow and attachments before marking publication complete. Keep the verified `v0.9.1` record below as history.

## Release checklist

Run commands from the repository root in the same Bash session. **Stop if any
command fails.** `0.9.2` is an example for a future release, not a version to reuse
unconditionally. Published tags such as `v0.9.1` must not be moved or overwritten.

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
next_version=0.9.2
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

Use a temporary virtual environment and a fresh output directory to avoid global
Python changes or accidentally checking a stale package:

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

Review skipped tests: missing browser tools can cause local skips, not full browser
coverage. CI tests on Python 3.8 and 3.12, including Chromium on 3.12. It builds
both distributions, runs Twine checks, and installs each into fresh virtual
environments outside the checkout, without extras and then with `[all]`.
Installed-package smoke tests cover command entry points, embedded resources,
reports/timeline/comparison, JSON export, and a compiled ELF resolver fixture.
The Python 3.12 packages and checksums become the `python-dist` Actions artifact.

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
prerelease status, and attaches exactly the wheel, sdist, and checksum file:

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

The printed version must match `next_version`, and both checksums must pass.
For full installed-package validation of the downloaded wheel, without importing
the source checkout:

```bash
"$download_dir/venv/bin/python" -I "$PWD/tests/smoke_installed.py" --with-extras
```

This smoke test also needs a C compiler and binutils. Source installations can be
checked in another fresh environment using the downloaded `.tar.gz` instead of
the wheel. Record the tag, commit, run IDs, and verification outcome before
declaring the release complete.

## Install a release package

Download the wheel or source distribution and install it by local path, as in the
verification commands above; `pip install funcgraph` is not part of this workflow.
The base package has no Python runtime dependencies. Extras `fast`, `highlight`,
and `all` install pyelftools, Pygments, or both. Installed commands are `funcgraph`,
`fgreport`, `fastfaddr2line`, and `fgcapture.sh`. Fast resolution still requires
the appropriate system `addr2line`; capture requires Bash, root, and tracefs on
Linux. Source-based commands (`python3 funcgraph.py`, `./fgreport`) remain supported.
Both distributions include the resolver and CSS/JavaScript resources; source
assets live in `assets/`, while wheels install them under `fglib/_assets/`.

## Troubleshooting

| Symptom | Check / action |
| --- | --- |
| Feature branch push has no Actions run | Expected without a PR. Open a PR or manually run `ci`; only `master` branch pushes trigger CI automatically. |
| `master` CI passed, but no Release | Branch pushes do not release. Check that the matching tag exists on `origin` and inspect its `release` run. |
| `check-tag` rejects a version | Compare the tag with `APP_VERSION` in the **tagged commit**, not the current branch. |
| Test, packaging, or resource smoke test failed | Inspect the failed job; repository imports working locally do not prove installed resources are included. |
| Artifact/checksum failure or publication permission denied | Inspect `build`/`publish` logs, the `python-dist` artifact, and repository/organization Actions policies; the publish job needs `contents: write`. |
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

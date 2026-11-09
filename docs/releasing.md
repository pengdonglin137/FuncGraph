# Building and releasing FuncGraph

## Install a release

Download the `.whl` or `.tar.gz` attachment from a GitHub Release, then install it:

```bash
python3 -m pip install './funcgraph-0.9.0-py3-none-any.whl[all]'
fgreport -P trace.txt
funcgraph --timeline --output report.html trace.txt
```

The base package has no Python runtime dependencies. Extras `fast`, `highlight`,
and `all` install pyelftools, Pygments, or both. The package also installs
`fastfaddr2line` and `fgcapture.sh`. Fast address resolution still needs the
appropriate system `addr2line`; capture needs Bash, root, and tracefs on Linux.
Source-based commands (`python3 funcgraph.py`, `./fgreport`) remain supported.

## Build locally

```bash
python3 -m pip install build twine
python3 -m build
python3 -m twine check dist/*.whl dist/*.tar.gz
```

`pyproject.toml` reads the version from `fglib/constants.py:APP_VERSION`.
Both distributions include the resolver and all CSS/JavaScript assets. In a
checkout the assets stay in `assets/`; wheels install them under `fglib/_assets/`.

CI runs the existing suite, builds and checks both distributions on Python 3.8
and 3.12, and installs each into a fresh virtual environment. Installed-package
smoke tests run outside the checkout, first without extras and then with all
extras. They cover command entry points, embedded resources, report generation,
comparison, JSON export, and address resolution against a small compiled ELF.
The Python 3.12 build is retained as the `python-dist` Actions artifact.
The CI and release workflows share `.github/workflows/test-and-package.yml`;
GitHub Pages deployment remains separate from release permissions.

## Publish a GitHub Release

1. Update `APP_VERSION` in `fglib/constants.py` and the documented version as needed.
2. Commit and push the changes, including the workflows, to GitHub.
3. Create and push a matching tag, for example after setting `APP_VERSION = "0.9.1"`:

```bash
git tag -a v0.9.1 -m 'FuncGraph 0.9.1'
git push origin v0.9.1
```

Pushing `v*` triggers `.github/workflows/release.yml`. It rejects tags that do
not exactly match `v` plus `APP_VERSION`, reuses the CI test/package jobs, checks
the artifact checksums, and only then creates a GitHub Release with generated
release notes. Attachments are the wheel, source distribution, and `SHA256SUMS`.
Versions such as `0.10.0rc1`, `0.10.0a1`, and `0.10.0b1` create prereleases and
are not marked as the latest release. A failed check prevents publication.

GitHub Actions must be enabled. Publication uses the repository's automatic
`GITHUB_TOKEN`, scoped to `contents: write` only in the publishing job; no
personal access token or PyPI token is needed. Use a normal Git push to trigger
the workflow: tags pushed by another workflow using `GITHUB_TOKEN` do not trigger
new workflow runs. Existing releases are not overwritten; fix failures and rerun
before publication, or use a new version after publication.

This workflow publishes **GitHub Releases**, not PyPI. Publishing to PyPI is a
separate step requiring a package name and a configured publishing identity.

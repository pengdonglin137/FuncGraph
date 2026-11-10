# Local Report Viewer

`fgview` is an optional, read-only HTTP viewer for an existing report. Ordinary FuncGraph HTML still opens offline by double-clicking. The viewer adds no third-party dependencies and does not generate sidecar output, virtualize Text, choose a size threshold or reduce the report's DOM/memory footprint.

## Usage

After installing FuncGraph:

```bash
fgview trace.html
fgview trace.html --open
fgview /path/to/dedicated-report-directory --port 9000
```

From the source checkout, use `./fgview trace.html` or `python3 -m fglib.viewer trace.html`.

- The first output line is the exact URL to open. It contains a fresh random path for this server instance.
- The listener binds only to `127.0.0.1`. The default port is chosen automatically; `--port 0` also requests an available port. There is no public-interface binding option.
- No browser launches unless you specify `--open`. If browser launching fails, the server keeps running and you can open the printed URL manually.
- Stop the server with Ctrl+C. Its URL stops working; a new run has a different random path.
- `--help` lists options and `--version` reports the installed FuncGraph version.

Browser preferences belong to the HTTP origin. Use the same explicit `--port` and hostname across runs if you want to reuse saved view/fold/filter settings; an automatically selected port may change that origin. HTTP preferences are separate from `file://` preferences. The random path itself does not change the origin.

## File And Directory Modes

**File mode** serves only the selected HTML file. Siblings are not accessible, even if their names are known. This is appropriate for current self-contained reports and does not expose the surrounding source/work directory.

**Directory mode** requires `index.html` and serves ordinary non-hidden files below the selected directory, including nested JSON resources. It has no directory listing. Use a dedicated report directory: this mode is not a manifest-only allowlist and must not point at an entire source/work directory containing other private files. External-resource prototypes and future sidecar bundles use this mode so relative fetches work. The CLI does not yet produce these bundles.

The viewer fixes the selected root through an open directory handle. Symlinks below that root, traversal paths, hidden paths, FIFOs and other non-regular files are not served. Selecting a path that is itself a symlink explicitly selects its resolved target. Platforms must support directory-relative, no-follow file access; unsupported platforms fail clearly instead of falling back to an unrestricted server.

## Security And Loading

Only GET and HEAD are supported. Requests require a valid loopback Host and the random path; explicit cross-origin requests are rejected. The viewer does not enable CORS or offer upload, editing, deletion or shell endpoints. Responses disable caching and MIME sniffing, restrict cross-origin use and apply a Content Security Policy compatible with the generated report's inline scripts/styles and same-origin fetches.

Use trusted reports. Their JavaScript still runs in the browser; the viewer is not an HTML sanitizer or a security sandbox. The random URL is not multi-user authentication, and local users who obtain it can read the selected resources. Do not treat it as a public report-sharing service.

Startup does not parse or eagerly read Timeline, Text or source resources. Files stream in chunks of at most 1 MiB; HEAD does not read the body. This bounds the viewer's transfer buffer, not the browser's memory. Report integrity checks, cancellation and retry remain the responsibility of `fgData`; ordinary reports still contain all their data.

## Troubleshooting

- **Sidecar data needs HTTP**: run `fgview REPORT_DIRECTORY` on the dedicated directory containing `index.html`, then use its printed HTTP URL rather than `file://`.
- **404**: check that the resource exists, is a regular non-hidden file and does not traverse a symlink. File mode intentionally refuses sibling resources; use directory mode for a complete external-resource bundle.
- **403**: use the printed URL, not a custom hostname or another origin. The viewer intentionally rejects those requests.
- **Port in use**: omit `--port` or use `--port 0` to choose a free port.
- **Missing index.html**: directory mode does not browse folders. Select a single HTML file or the correct dedicated report directory.
- **Unsupported safe file access**: use a platform with directory-relative no-follow access. There is no unsafe fallback. Self-contained HTML can still be opened offline without the viewer.

中文：[本地报告查看器](viewer.cn.md)。Sidecar work remains tracked in the [loading plan](plans/2026-11-10-sidecar-lazy-loading.md).

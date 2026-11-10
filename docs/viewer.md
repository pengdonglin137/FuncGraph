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

**Directory mode** requires `index.html` and serves ordinary non-hidden files below the selected directory, including nested JSON resources. It has no directory listing. Use a dedicated report directory: this mode is not a manifest-only allowlist and must not point at an entire source/work directory containing other private files. Sidecar bundles use this mode so relative fetches work.

## Generate A Sidecar Report

```bash
funcgraph trace.txt --timeline --report-mode sidecar --enable-fold --filter --output trace-report
fgview trace-report --open
```

Text and binary raw inputs use the same output mode; raw input still requires its optional native dependencies. `--report-mode single` remains the default and never switches automatically. Sidecar requires `--timeline`, an HTTP URL, and a dedicated output directory, not a filename.

### Automatic selection above an explicit byte threshold

```bash
# Example threshold: 64 MiB, not a built-in or universally recommended default.
funcgraph trace.txt --timeline --report-mode auto --sidecar-threshold 67108864 --output trace-report
fgview trace-report --open
# The wrapper chooses trace.report and keeps Text available, including above 150k lines.
fgreport --report-mode auto --sidecar-threshold 67108864 --open trace.txt
```

Auto mode also requires `--timeline` and a dedicated output **directory**, regardless of its decision. It counts the complete hypothetical single HTML's uncompressed UTF-8 bytes, including Text, embedded sources, view data and scripts. Only a strict `>` selects sidecar: equal or smaller stays a self-contained `index.html` that opens offline. The threshold is a nonnegative integer byte count; `0` forces sidecar for any nonempty report. There is **no default threshold**, and auto mode without one fails before processing. Trace line counts, compressed file sizes and shell sizes do not select the mode. This avoids treating equally long traces with different embedded-source sizes as equivalent.

The CLI prints the actual mode, full HTML byte count, threshold and entry path; sidecar decisions also print a shell-safe `fgview` command. `fgview` works for either decision. `fgreport --open` in a directory mode runs the loopback viewer in the foreground until Ctrl+C, not a `file://` directory URL. Both options can be stored in INI defaults/presets or with `fgreport init NAME --report-mode auto --sidecar-threshold BYTES`. Explicit `--report-mode single` overrides configured auto mode and writes the ordinary HTML file. Wrapper single mode retains its existing 150k-line Text omission rule (`--text` overrides it); sidecar/auto preserve Text unless explicitly omitted.

Generation still renders the full report once and stages the filter index even for a below-threshold decision, then discards that index for single output. It needs temporary disk space for the full HTML plus index and, above threshold, the extracted resources; it does not skip parsing or symbol resolution. Updating a managed directory may switch in either direction without moving its entry path. Old sidecar resources remain available to already-open pages even after a switch back to single; do not interpret those historical directories as evidence that the current entry uses sidecar. Fresh single decisions publish only `index.html` and the hidden ownership marker. The same publication protections described below apply to both decisions.

### Sidecar loading

- First Timeline loads its complete model but does not fetch Text or Compare and creates no per-record Text DOM. Sources and their index remain embedded and parse synchronously when used.
- Timeline unified filtering fetches `text-index.json` on first use, evaluates all trace rows without creating Text DOM, and shares the Text expression/graph semantics. Progress is cancellable with Escape or Clear; a newer query supersedes the previous one. Errors retain the previous result and offer **Retry filter**. The complete index is cached until Text mounts; downloading/parsing it and graph preparation still use main-thread memory and time.
- Text loads **in full once** when entering Text, jumping to a trace line or following `#L<number>`. The verified HTML is prepared off-document in complete record batches, with percentage progress and opportunities to return to Timeline, switch tabs or press Escape. Cancellation discards detached nodes but retains verified HTML for reentry without another download. No partial rows are exposed. Before attaching the complete result, **Finishing Text layout…** gets a paint opportunity; the final initialization/layout is still synchronous and can block. Its active filter is reapplied before use, and the index/HTML-string caches are released on success. The complete Text DOM then remains cached; it is not virtualized or bounded, and total opening time is not necessarily shorter. Saved Text preferences and hash anchors open Text directly. Older sidecar reports without an index retain full-Text filtering.
- Fetch/integrity and detached Text parsing failures expose Retry; Escape/back cancels pending view loading/preparation without allowing late data to activate it. A failure during final Text initialization instead offers **Reload report**, avoiding reuse of partially initialized listeners/state. Regenerate existing reports to use the new preparation behavior.
- Filtering shares a complete record model across indexed and mounted Text. Deferred Text reuses an already prepared index model, or snapshots every authored row while detached; it does not fetch the index just to prepare Text. Row snapshot/validation and complete fold metadata yield during preparation, with the usual status and cancellation controls. Only the complete model is published with the final mount, so the first fold reuses it; metadata failures before mounting can Retry, and cancellation discards partial work while retaining verified HTML. Embedded Text keeps lazy synchronous preparation. Generated fold summaries are not trace input, and task/duration reordering does not change stable metadata. Releasing the `fgData` input caches does not release this logical model: its rows and the full DOM remain resident. This moves work into visible preparation, not off the main thread; total readiness can increase, and initialization/native layout can still block. Navigation/display are not virtualized. Regenerate reports to use this behavior and the working `sort:asc` / `sort:desc` directives.
- `--no-text-view` still omits Text and never creates a Text resource; `--keep-source` retains its existing source-resolution semantics.
- Mounted Text also shares non-DOM fold membership/counts/visibility. Filtering and clearing keep collapsed ancestors closed; unfolding respects rejected rows and only restores visible saved source panels. Bulk fold state is projected once, not by repeated descendant DOM scans. Summary formatting, depth controls, navigation and full-DOM layout still require further decoupling; regenerate older reports to use these fixes.

The bundle contains `index.html`, a hidden ownership marker and immutable `data-<report-id>/` resources (manifest, Timeline, optional Compare and complete Text). Every resource has exact UTF-8 bytes and SHA-256 metadata. Share/archive the **whole directory**, not only index.html.

For the measured 72,176-line trace, median complete Timeline readiness drops from 1.87s to 0.34s and sampled renderer RSS from 758 to 202 MiB, while the first complete Text mount still costs 8.46s in one observation. These are local measurements, not guarantees; methodology, limitations and the repeatable benchmark are in [design §8.7](design.md#87-显式-sidecar-的首次-timeline2026-11-10).

Publication stages a complete bundle beside the destination. New/empty directories publish only after successful rendering and syncing; unmanaged nonempty directories, file targets and output symlinks are refused. Updates to marked bundles publish new versioned resources before atomically replacing index.html, preserving the old entry on failure. Old resource directories are intentionally retained for already-open pages, so repeated updates consume disk space; for cleanup, close old viewers/pages and remove the old bundle directory before regenerating, or publish to a new dedicated directory.

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

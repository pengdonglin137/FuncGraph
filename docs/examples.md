# FuncGraph by use case

Start with existing recordings, then decide whether to capture again. The offline
examples need no root, uftrace, QEMU or matching `vmlinux`. Recording a new workload
requires the appropriate target, instrumented program and privileges.
中文版：[examples.cn.md](examples.cn.md). See the [user guide](guide.en.md) for fields and controls.

## Choose a starting point

| Goal | Example | Requirements |
| --- | --- | --- |
| Explore kernel and mixed calls without recording | [Offline replay](#offline-replay) | Python package and the sample files in this checkout |
| Trace the kernel without rebuilding the application | [Kernel-only case](capture.md#case-3-kernel-only-recording-without-rebuilding-an-application) | Root and idle tracefs |
| Inspect layered user calls, arguments, returns and callers | [File copy and UDP](capture.md#case-1-user-file-copying-and-udp-calls) | Instrumentation, debug information and patched uftrace |
| Observe exec → user → kernel → exit in one run | [Mixed capture](capture.md#case-2-same-run-exec--user--kernel--exit) | User requirements plus root and dedicated tracefs |
| Trace a real tool and its dependency | [pigz + zlib](pigz.md) | Git, GCC, Make and binutils; the script pins both upstream commits |
| Reuse matching kernel sources across reports | [Kernel presets](#kernel-presets) | The image and sources matching the recorded kernel |
| Defer Text loading for a large report | [Large reports and the local viewer](#large-reports-and-the-local-viewer) | The complete report directory and `fgview` |
| Analyze a trace-cmd `trace.dat` | [Raw input guide](raw-input.md) | Optional native libraries and a C compiler; not bare `trace_pipe_raw` pages |

### Which command does what?

- `fgcapture`: recommended recorder; kernel-only by default, or `--user` / `--mixed`.
- `fguftrace`: the patched uftrace interface for native export and advanced
  recording; it does not automatically apply the tracefs protection of `fgcapture --mixed`.
- `funcgraph`: generates reports from text, raw containers, JSONL or `--uftrace DIR`.
- `fgreport`: adds common report options and kernel presets; it does not record.
- `fgview`: serves an existing report directory over local read-only HTTP; it does
  not record or convert traces.

User/mixed capture prefers the bundled uftrace; its path is not a required option.
Only native Linux x86_64 / aarch64 wheels include the recorder and libmcount;
Python-only wheels and source distributions do not. See
[installation](uftrace.md#install-and-select-the-recorder) for architecture,
runtime libraries and explicit recorder overrides.

## Offline replay

Install the package, obtain this repository and run from its root. Installing a
wheel does not put `samples/` in the current directory. Opening the existing
`sample.html` / `sample_mixed.html` needs no Python at all.

```bash
mkdir -p build/examples
funcgraph --no-config samples/sample_io.txt --timeline --enable-fold --filter \
    --output build/examples/kernel.html
funcgraph --no-config samples/sample_mixed.jsonl --timeline --enable-fold --filter \
    --user-src samples --output build/examples/mixed.html
```

Open both HTML files directly in a browser. `--no-config` avoids unrelated kernel
paths in personal configuration. These commands do not recreate the kernel
source/BTF enrichment in the published HTML; without a matching image they do not
guess kernel source positions. The mixed JSONL already contains argument and
caller metadata, so reading it requires no local uftrace executable.

| Report | Suggested action | Check |
| --- | --- | --- |
| `kernel.html` | Find `__x64_sys_recvfrom` and inspect its return | Empty nonblocking receive returns `-EAGAIN`; this does not fail the whole run |
| `mixed.html` | Select `dispatch_datagram` | Five named argument values and Call site `send_frame+offset/size` |
| `mixed.html` | Select each `receive_frame` call | Different caller lines, `demo_mixed.c:47` and `:49`, not the function entry |
| `mixed.html` | Enter `__x64_sys_sendto` | Matched user/kernel boundary markers and address-space separation in the call stack |

Here `--user-src samples` is specific to the **existing public JSONL**, which stores
`demo_mixed.c`. Fresh recordings from the [build/capture case](capture.md#quick-start)
usually store `samples/demo_mixed.c`, requiring `--user-src "$PWD"` from the checkout
root. Use the recorded path; choosing a source root does not rewrite paths.

### Shorter commands and comparison with fgreport

```bash
fgreport -P samples/sample_mixed.jsonl -o build/examples/mixed-wrapper.html -- \
    --no-config --user-src "$PWD/samples"
fgreport -P -c samples/sample_io.txt samples/sample_io_slow.txt \
    -o build/examples/compare.html -- --no-config
```

`-P` disables kernel presets; options after `--` go to `funcgraph`, with
`--no-config` also disabling personal configuration. Do not put `--user-src` in
the wrapper's own option list. Compare uses `sample_io.txt` as the baseline and
`sample_io_slow.txt` as the current trace. Different kernels and recording overhead
make these an interaction demo, not evidence of a performance regression.

## Kernel presets

First follow [mixed capture](capture.md) to produce `mixed.data/`. Set the variables
below to the kernel that was **recorded**, not the analysis host's current kernel.
The web revision must exist in the selected repository. For a custom kernel,
`KERNEL_WEB_REV` can name its upstream baseline, but document possible line-number
differences from the custom sources.

```bash
fgreport init target-kernel --vmlinux "$VMLINUX" --kernel-src "$KERNEL_SRC" \
    --source-rev "$KERNEL_REV" --source-browser github --source-browser-rev "$KERNEL_WEB_REV"
fgreport presets
fgreport -n -p target-kernel --uftrace mixed.data -o mixed-source.html -- \
    --user-src "$PWD"
fgreport -p target-kernel --uftrace mixed.data -o mixed-source.html -- \
    --user-src "$PWD"
```

`VMLINUX` is the matching image with debugging information and the BTF you need;
`KERNEL_SRC` is its source tree. `KERNEL_REV` pins embedded kernel source and
`KERNEL_WEB_REV` pins GitHub links. Inspect the command with `-n` before executing
without it. This explicitly selects a preset without changing the default preset.
Pass `--user-src` separately; never reuse the kernel source root or kernel web
links for user code. Use the earlier no-preset workflow when only structure is
needed instead of supplying an unrelated `vmlinux`.

## Large reports and the local viewer

Practice directory output with the small sample before substituting a large trace:

```bash
funcgraph --no-config samples/sample_mixed.jsonl --timeline --enable-fold --filter \
    --user-src samples --report-mode sidecar --output build/examples/mixed.report
fgview build/examples/mixed.report --open
```

Use the printed local HTTP address; Ctrl+C stops the server. Share the **complete
directory**, not just `index.html`, and do not open its entry point through
`file://`. Timeline is the default initial view; saved Text preferences or line
anchors may override it. Text loads on first use. Once opened, the complete Text
model and DOM remain resident: sidecar is not windowing or an overall memory bound.

To switch only above an explicit complete-HTML byte threshold, use auto mode. The
64 MiB below is an example, not a default:

```bash
fgreport -P --report-mode auto --sidecar-threshold 67108864 \
    samples/sample_io.txt -o build/examples/kernel-auto.report -- --no-config
```

At or below the threshold, the dedicated directory contains a single-file
`index.html`. Do not mix report output with input traces or source files. See the
[viewer guide](viewer.md) for loading, cancellation, retry and Timeline-only options.

## What to retain after recording

The [unified capture guide](capture.md) collects compilation, execution, validation
and restoration steps; there is no need to assemble native options from different
pages. The pigz case also checks that decompression reproduces the input.

| Stage | Retain and verify |
| --- | --- |
| Before running | Instrumentation/build flags, application/library commits, recorded kernel version, matching image and source revisions |
| After recording | Complete `*.data/` and adjacent `*.data.fgcapture/`; check restoration, exit status and lost records |
| Before offline analysis | Export with `fguftrace dump --fgraph --srcline -d mixed.data > mixed.jsonl`; preserve source-root mapping |
| Sharing | Share a single HTML directly, or the entire sidecar directory; reports do not replace original data for re-export |

Regenerating a report cannot recover arguments, callers, lost events or source
mappings absent from an old recording. `complete:true` means export ended, not
that no events were lost. An exec-to-exit time span does not imply every function
was recorded. Consult [troubleshooting](capture.md#preserve-share-and-troubleshoot)
before changing tracefs; do not clear somebody else's recording session.

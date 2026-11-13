# FuncGraph

**Follow Linux execution from application functions into the kernel.**

Turn ftrace and uftrace recordings into interactive reports: explore call trees,
find where time went, inspect arguments and return values, and jump to source.
Use kernel-only, user-only or mixed recordings—without changing how you read them.
The default output is a single HTML file you can open offline.

[English](README.md) · [简体中文](README.cn.md) ·
[Live demos](https://pengdonglin137.github.io/FuncGraph/) ·
[User guide](docs/guide.en.md) ·
[Releases](https://github.com/pengdonglin137/FuncGraph/releases)

[![CI](https://github.com/pengdonglin137/FuncGraph/actions/workflows/ci.yml/badge.svg)](https://github.com/pengdonglin137/FuncGraph/actions/workflows/ci.yml)

[![Mixed user/kernel Timeline with recorded arguments and source](sample_mixed.png)](https://pengdonglin137.github.io/FuncGraph/sample_mixed.html)

**Try the [mixed report](https://pengdonglin137.github.io/FuncGraph/sample_mixed.html):**
follow `main → run_round → exchange_loopback → send_frame → dispatch_datagram → sendto`
into `__x64_sys_sendto`. Select `dispatch_datagram` for named arguments, its return
value and caller site; the divider marks a matched user/kernel boundary.
Or explore the [kernel-only report](https://pengdonglin137.github.io/FuncGraph/sample.html)
and its [source/error-path screenshot](sample.png). No installation required.

## What can you investigate?

| Question | Explore it with |
| --- | --- |
| Which functions ran, and in what order? | **Timeline** task and CPU tracks, nested slices, matched user/kernel boundaries, and recorded exec/exit events. |
| Where did the time go? | Total/self-time colors, function statistics, slow calls, off-CPU intervals, wakeup paths and wait chains. Self time is not necessarily on-CPU time. |
| Why did this call fail? | Recorded arguments and returns, BTF-aware kernel decoding, errno propagation and linked caller/source details. |
| Was execution delayed or in a critical section? | Recorded context flags, IRQ/preemption regions and scheduler latency. These need the corresponding trace fields/events. |
| How do I read or compare the evidence? | **Text** with folding, filtering, source expansion and keyboard navigation; **Compare** for per-function differences; JSON/CSV export for scripts. |

Timeline offers full-screen viewing, resizable details, compact menus and
light/dark themes. Text and Timeline share reachable view tabs and trace-line
navigation. **Tools → Saved views…** keeps named analysis positions and notes,
with export/import for someone using the same report data.
Missing events or debug information are not replaced with invented
callers, parameters or timing.

**On this page:** [Install](#install) · [Quick start](#quick-start) ·
[Capture](#capture-user-and-kernel-calls) · [Source and types](#source-and-type-information) ·
[Large traces](#large-traces) · [Documentation](#documentation)

## Install

Python **3.8+** is required for report generation. Reading ordinary text or a saved
FuncGraph uftrace JSONL export does not require a recorder, root or `vmlinux`.

Download a wheel from [GitHub Releases](https://github.com/pengdonglin137/FuncGraph/releases)
and install the local file. For recording on x86_64 Linux:

```bash
python3 -m pip install './funcgraph-<version>-py3-none-linux_x86_64.whl[all]'
fguftrace --version
```

Replace `<version>` with the downloaded filename. Choose `linux_aarch64` on ARM64.
Both platform wheels include **patched uftrace and its libmcount recording
libraries**; `fgcapture`, `funcgraph` and `fgreport` select the bundled recorder
automatically. `--uftrace-bin PATH` is an override, not a required argument.

The `py3-none-any` wheel and source distribution are Python-only. Native wheels
are built on Ubuntu 22.04, are not manylinux, and still require compatible glibc
and system libraries. `[all]` adds pyelftools and Pygments, not those system
libraries. See [package selection and requirements](docs/capture.md#choose-a-package-and-capture-mode).
The release workflow does not publish to PyPI.

For report development or replaying the repository's samples, clone the project
and run from its root:

```bash
git clone https://github.com/pengdonglin137/FuncGraph.git
cd FuncGraph
python3 -m pip install '.[all]'
```

This source install does **not** compile uftrace. Use a native wheel or follow the
[pinned native build instructions](docs/uftrace.md#reproducible-native-build)
for fresh user/mixed recording. Text and saved JSONL replay need no submodules.

## Quick start

### Open your existing trace

```bash
funcgraph --no-config trace.txt --timeline --enable-fold --filter --output report.html
```

Open `report.html` directly in a browser. Start in Timeline, select a call, then
follow its trace-line link into Text. `--no-config` keeps unrelated personal
kernel presets out of this first run; source enrichment is optional.

| Input | How to use it | Additional requirement |
| --- | --- | --- |
| ftrace `function_graph` text | Pass `trace.txt`; `.gz`, `.xz`, `.bz2`, stdin and multiple files also work. | None for call visualization. |
| trace-cmd binary container | Pass `trace.dat`; format detection is automatic. | Optional native decoding libraries and a host C compiler; [raw input setup](docs/raw-input.md). Bare `trace_pipe_raw` pages are not standalone inputs. |
| uftrace recording directory | Use `--uftrace capture.data` instead of a trace file. | FuncGraph's patched uftrace, selected automatically when bundled. |
| FuncGraph uftrace JSONL export | Pass `capture.jsonl` directly. | No local uftrace needed; not an arbitrary upstream JSON format. |

### Replay the included samples

From the checkout root after installation:

```bash
mkdir -p build/examples
funcgraph --no-config samples/sample_io.txt --timeline --enable-fold --filter \
    --output build/examples/kernel.html
funcgraph --no-config samples/sample_mixed.jsonl --timeline --enable-fold --filter \
    --user-src samples --output build/examples/mixed.html
```

These reproduce the call structure and recorded values without root, QEMU,
uftrace or a matching kernel image. They do not recreate the published report's
kernel source/BTF enrichment without the matching debug files. A wheel install
does not put `samples/` in your working directory.

## Capture user and kernel calls

One entry point, three modes:

| Mode | Captures | Needs |
| --- | --- | --- |
| `fgcapture --kernel` | A command's kernel function graphs; no application rebuild. | Root, Linux tracefs and Bash; no uftrace. |
| `fgcapture --user` | Instrumented application/library functions, arguments, returns and caller sites. | Patched uftrace and suitable build/debug information; no root or tracefs. |
| `fgcapture --mixed` | User/kernel calls in one recording, plus available scheduling and exec/exit events. | User-mode requirements, root and idle dedicated tracefs. |

For an existing program's kernel path:

```bash
sudo "$(command -v fgcapture)" --kernel --sched -o kernel.txt -- cat /etc/hostname
fgreport -P kernel.txt
```

For application-to-kernel exploration, use the layered file-copy/UDP demo.
Run from the checkout root; the following compiler flags are for **x86_64**.
On **ARM64**, replace `-pg` with `-fpatchable-function-entry=2`.

```bash
cc -pg -g -O0 -fno-omit-frame-pointer -o demo_mixed samples/demo_mixed.c
printf 'FuncGraph demo\n' > demo-input.txt
: > demo-output.txt
fgcapture --user -o user.data -- ./demo_mixed demo-input.txt demo-output.txt
funcgraph --no-config --uftrace user.data --timeline --enable-fold --filter \
    --user-src "$PWD" --output user.html
```

On an **idle experimental host**, capture the same program in mixed mode:

```bash
: > demo-output.txt
sudo "$(command -v fgcapture)" --mixed -o mixed.data -- ./demo_mixed demo-input.txt demo-output.txt
fgreport -P --uftrace mixed.data -- --user-src "$PWD"
```

Use a new recording directory for each run. User/mixed modes request arguments,
returns and call sites by default; actual coverage depends on instrumentation
and recorded metadata. Kernel-only mode probes argument support before enabling
it. Mixed capture checks and restores tracefs settings, including on failure;
it defaults to syscall roots and **eight kernel levels**, not all kernel code.
It is not a production tracing isolation mechanism.

See the [complete capture cases](docs/capture.md) for scope controls, scheduling,
exec/exit checks and troubleshooting. For a real tool and instrumented dependency,
follow the [pigz + zlib case](docs/pigz.md): build flags, bounded workload,
recording, both source trees and saved-artifact checks.

## Source and type information

Keep **user and kernel sources separate**. For a mixed recording, replace the
paths and revisions with those of the kernel **running during capture**, not the
machine generating the report:

```bash
VMLINUX=/path/to/matching/vmlinux
KERNEL_SRC=/path/to/linux
KERNEL_REV=recorded-kernel-commit
KERNEL_WEB_REV=matching-upstream-tag-or-commit
funcgraph --no-config --uftrace mixed.data --timeline --enable-fold --filter \
    --user-src "$PWD" --vmlinux "$VMLINUX" --kernel-src "$KERNEL_SRC" \
    --source-rev "$KERNEL_REV" --func-links --highlight-code --fast \
    --source-browser github --source-browser-rev "$KERNEL_WEB_REV" --output mixed.html
```

- **User code:** `--user-src` allows local source embedding. Choose the root from
  recorded paths: fresh captures above use `"$PWD"`, while the saved public JSONL
  uses `samples`. User paths are never sent to a kernel source browser.
- **Kernel code:** a matching `vmlinux`, source tree and toolchain resolve call
  sites and inline frames. `--source-rev` chooses embedded kernel content;
  `--source-browser-rev` independently chooses web links. GitHub's
  `torvalds/linux` is the default browser; `--source-browser elixir` is available.
  Pin a matching tag/commit rather than relying on moving `master`/`latest` links.
- **Arguments and returns:** available kernel BTF corrects known parameter
  counts/bit widths and return interpretation. User parameter names/types come
  from available recorded DWARF/specifications, not kernel BTF. Raw evidence and
  unsupported/missing information remain distinguishable.

Save repeated kernel settings in a preset instead of retyping them:

```bash
fgreport init target --vmlinux "$VMLINUX" --kernel-src "$KERNEL_SRC" \
    --source-rev "$KERNEL_REV" --source-browser github --source-browser-rev "$KERNEL_WEB_REV" --default
fgreport trace.txt
fgreport --uftrace mixed.data -- --user-src "$PWD"
```

See the [source/configuration guide](docs/guide.en.md#4-generating-a-report) for
modules, cross-toolchains, path remapping and custom source browsers.

## Large traces

**Single HTML remains the default.** Complete inline Text at 4,096 or more rows
automatically skips offscreen rendering in supported browsers. This speeds
repeated Timeline → Text entry without discarding rows, source, native selection
or horizontal reading space. Initial preparation can still be expensive; see
[measurements and limits](docs/performance-view-roundtrip.md).

For a report that should load Timeline before Text, choose a **sidecar directory**:

```bash
funcgraph --no-config trace.txt --timeline --enable-fold --filter \
    --report-mode sidecar --output trace.report
fgview trace.report --open
```

- Sidecar loads Text and source resources when needed; Compare loads separately.
  Serve it with `fgview`, and share the **whole directory**, not just `index.html`.
- To switch only above a size you choose, use `--report-mode auto` with
  `--sidecar-threshold 67108864` and a directory output. This example is **64 MiB
  of complete HTML**, not a built-in default or a trace-line threshold.
- Default Text still prepares and retains the complete DOM/model. Experimental
  **Windowed Text** is opt-in for newly generated chunked sidecars; it limits
  mounted rows, **not total report memory**. [Windowed-mode guide](docs/performance-text-window.md).
- In single mode, `fgreport` chooses Timeline-only above 150k input lines;
  `--text` keeps Text. Directory modes retain Text. Explicit
  `--no-text-view --keep-source` is another option for source-enabled Timeline-only reports.

See the [viewer guide](docs/viewer.md) for serving, automatic selection and
resource-loading behavior. `fgview` binds to loopback; serve a dedicated report
directory, not an entire source/work tree.

## Documentation

| Goal | Start here |
| --- | --- |
| Learn controls, filters, colors and analysis panels | [User guide](docs/guide.en.md) |
| Replay, use presets, compare runs or export JSON/CSV | [Scenario examples](docs/examples.md) |
| Record kernel, user or mixed execution | [Capture cases](docs/capture.md) |
| Build and trace a real tool with its dependency | [pigz + zlib](docs/pigz.md) |
| Install/build uftrace; inspect recording limits | [uftrace integration](docs/uftrace.md) |
| Read trace-cmd binary inputs | [Raw input](docs/raw-input.md) |
| Serve large reports and choose delivery modes | [Local viewer](docs/viewer.md) |
| Understand implementation or publish a release | [Design](docs/design.md) · [Release checklist and verified records](docs/releasing.md) |

## Development and validation

```bash
tests/run.sh
```

The suite covers parsing, analysis, CLI workflows and browser interactions.
Playwright/Chromium and Node enable browser/JavaScript checks; optional native
and real-kernel cases need their documented dependencies. See
[validation setup](docs/releasing.md#2-validate-locally) before treating skipped
optional tests as coverage. Third-party sources are pinned by commit, and local
uftrace changes are maintained as a checked patch series:
[dependency pins](third_party/README.md) · [uftrace patches](patches/uftrace/README.md).

Background articles (Chinese): [introduction](https://mp.weixin.qq.com/s/xRVVgF5IDnLXGu2i-TbS5Q) ·
[follow-up](https://mp.weixin.qq.com/s/Mq8uTR3c8V1gAR2zklsFPw) ·
[implementation story](https://mp.weixin.qq.com/s/rNiWXC8YlZiAjfcjv7QtQA).

# Unified recording with fgcapture

`fgcapture` keeps the kernel-only text recorder and adds user-only and mixed
recording through the same entry point. `fgcapture.sh --user` / `--mixed`
also delegate to it when the Python package is installed.

To explore saved recordings without preparing a capture environment, start with
[offline replay, presets, comparison and large-report examples](examples.md).

## Choose a package and capture mode

Download the wheel for the **recording host** from GitHub Releases and install
the local file; this release workflow does not publish to PyPI. Replace
`<version>` with the downloaded version:

```bash
uname -m
python3 -m pip install './funcgraph-<version>-py3-none-linux_x86_64.whl[all]'
fgcapture --help
fguftrace --version
```

| Package / use | Selection and requirements |
| --- | --- |
| x86_64 Linux recorder | `linux_x86_64.whl`, including patched uftrace and five libmcount libraries |
| ARM64 Linux recorder | `linux_aarch64.whl`, also including the recorder and libraries |
| Python-only wheel / sdist | No compiled uftrace; user/mixed recording requires a separate patched build |
| Reading saved JSONL | No uftrace needed; call visualization alone needs no vmlinux |

Native wheels are not fully static. Ubuntu needs `libtraceevent1`, `libelf1`,
`libdw1`, `liblzma5`, `libstdc++6` and compatible glibc; see
[installation and architecture notes](uftrace.md#install-and-select-the-recorder).
The `[all]` Python extra does not install system shared libraries.

| What to observe | Entry point | Recording requirements | Output |
| --- | --- | --- | --- |
| An existing program's kernel calls, without rebuilding it | `sudo fgcapture --kernel ... -- CMD` | Linux, root, Bash, tracefs; no uftrace | Text file |
| Application and instrumented library functions | `fgcapture --user ... -- CMD` | Patched uftrace, instrumentation and debugging information; no root / tracefs | uftrace directory |
| Same-run user/kernel calls and lifecycle | `sudo fgcapture --mixed ... -- CMD` | User requirements plus root and idle dedicated tracefs | uftrace directory and adjacent restoration evidence |

`fgcapture`, `fguftrace` and `funcgraph --uftrace DIR` prefer the bundled recorder
and matching libraries. `--uftrace-bin PATH` is only an override. Run the examples
from a FuncGraph checkout; installing a wheel does not create
`samples/demo_mixed.c` in the working directory.

## Quick start

Install the Linux wheel with bundled uftrace. Python-only installations need a
patched uftrace on PATH. The bundled executable and recording libraries are
selected automatically; `--uftrace-bin PATH` is an optional override.

```bash
# x86_64; use -fpatchable-function-entry=2 instead of -pg on ARM64
cc -pg -g -O0 -fno-omit-frame-pointer -o demo_mixed samples/demo_mixed.c
printf 'FuncGraph demo\n' > demo-input.txt
: > demo-output.txt
fgcapture --user -o user.data -- ./demo_mixed demo-input.txt demo-output.txt
sudo "$(command -v fgcapture)" --mixed -o mixed.data -- ./demo_mixed demo-input.txt demo-output.txt
funcgraph --no-config --uftrace mixed.data --timeline --enable-fold --filter \
    --user-src "$PWD" --output mixed.html
```

User-only recording needs no root and never inspects tracefs. Mixed recording
is for an idle dedicated experimental host. Add matching `--vmlinux`,
`--kernel-src`, `--func-links --highlight-code` and a pinned source revision
when generating reports with kernel BTF/source. `--user-src` selects the
separate user source tree; it never receives kernel web links.

The demo opens its output with `O_WRONLY`, not `O_CREAT`: create it first as
shown. Check the copied data with `cmp demo-input.txt demo-output.txt`. Use a
fresh capture directory for every run, and truncate a reused output again with
`: > demo-output.txt` to avoid retaining an older file's trailing bytes.

Choose the source root from the **exported paths**, not automatically the source
file's parent. This case records `samples/demo_mixed.c`, so use `--user-src "$PWD"`
from the checkout root; `samples` would look for `samples/samples/demo_mixed.c`.
Even an absolute compiler input can be saved as a relative path by uftrace.
The existing `sample_mixed.jsonl` instead records `demo_mixed.c`, so its rebuild
still uses `--user-src samples`. Absolute recorded paths must lie within the
allowed source root; copying a tree does not rewrite them. Check the exported
`source.file` / call-site source paths.

## Case 1: user file copying and UDP calls

The demo combines file copying with a loopback UDP exchange:

```text
main → run_round → transfer_file → read_block / write_block
                 → exchange_loopback → send_frame → dispatch_datagram → sendto
                                     → receive_frame → recvfrom
```

Run the user-only command above, then generate a report or save a portable export:

```bash
funcgraph --no-config --uftrace user.data --timeline --enable-fold --filter \
    --user-src "$PWD" --output user.html
fguftrace dump --fgraph --srcline -d user.data > user.jsonl
```

In Timeline, find `dispatch_datagram`. Its details should show the five named
parameters `fd`, `buffer`, `length`, `peer`, `peer_size`, with a
`send_frame+0x…/0x…` call site rather than the callee's entry. User names come
from available DWARF / recorded specifications, not kernel BTF. The two
`receive_frame` calls have distinct caller lines. The second nonblocking receive
is expected to return `-1` with `EAGAIN`; it is not failure of the whole demo.
Void, parameterless and unsupported ABI cases do not receive fabricated values.
Open source in Text or details and verify that user paths do not link to a kernel
repository.

## Case 2: same-run exec → user → kernel → exit

Compile and prepare input as above. On an idle dedicated VM, start with a small
kernel scope:

```bash
case "$(uname -m)" in
    x86_64) syscall_prefix=__x64_sys ;;
    aarch64|arm64) syscall_prefix=__arm64_sys ;;
    *) printf 'This example covers x86_64 / ARM64 only\n' >&2; exit 1 ;;
esac
: > demo-output.txt
sudo "$(command -v fgcapture)" --mixed --kernel-depth 4 \
    -g "${syscall_prefix}_sendto,${syscall_prefix}_recvfrom,do_execveat_common,do_exit" \
    -o mixed-example.data -- ./demo_mixed demo-input.txt demo-output.txt
cmp demo-input.txt demo-output.txt
```

This deliberately scopes kernel functions to network / exec / exit paths. File
copying still runs, but unselected kernel I/O calls are not invented. Check roots
against the target kernel's `available_filter_functions`; names can vary between
kernels. Omitting `-g` selects the architecture's syscall roots and depth 8, not
every kernel function.

Inspect structure without requiring a matching kernel image:

```bash
funcgraph --no-config --uftrace mixed-example.data --timeline --enable-fold --filter \
    --user-src "$PWD" --output mixed-example.html
fguftrace dump --fgraph --srcline -d mixed-example.data > mixed-example.jsonl
```

Check **Fit trace**, then **Tools → Exec / exit** for actual lifecycle events.
Space boundaries appear on matched syscalls; **Call site** uses symbol + offset.
Scheduler events, wakeups and incomplete calls reflect the actual recording.
Do not expect the task's CPU start time to equal `main`, or missing endpoints
to be filled in.

For kernel parameter names / widths, return types and clickable source, configure
the two source trees separately. These are values for the verified QEMU image;
replace all three with the image, source and commit for the **kernel running
during recording**, not the report host's current kernel:

```bash
KERNEL_SRC=/vol_1t/Qemu/linux
VMLINUX="$KERNEL_SRC/x86_64/vmlinux"
KERNEL_REV=7475ea8b58616b33c04afb976cf0fbaad9dd63fb
funcgraph --no-config --uftrace mixed-example.data --timeline --enable-fold --filter --fast \
    --user-src "$PWD" --vmlinux "$VMLINUX" --kernel-src "$KERNEL_SRC" \
    --source-rev "$KERNEL_REV" --source-browser github --source-browser-rev "$KERNEL_REV" \
    --func-links --highlight-code --output mixed-source.html
```

User source still uses `--user-src`; kernel web links do not use `master` or
`latest`. `--fast` uses pyelftools from `[all]`, avoiding repeated shell address
resolution for large call-site lists. Matching BTF corrects kernel types for recorded parameters, not missing
bytes. See [real capture acceptance](capture-validation.md) for observed results
and restoration checks, and [pigz + zlib](pigz.md) for a business/dependency case.

## Case 3: kernel-only recording without rebuilding an application

```bash
sudo "$(command -v fgcapture)" --kernel --sched -g vfs_read,vfs_write \
    -o kernel.txt -- cat samples/demo_mixed.c > /dev/null
funcgraph --no-config kernel.txt --timeline --enable-fold --filter --output kernel.html
```

This shows the command's kernel side, not `cat` user functions. Text input needs
no uftrace; add kernel source options as in the previous example. Do not pass
this file to `--uftrace`, which expects a recording **directory**.

## Defaults and controls

- Kernel-only mode forwards the existing duration/PID/graph/calibration options
  to `fgcapture.sh`. Arguments now use a small automatic probe: enable them only
  when fast and compatible with return addresses. Otherwise retain call sites
  and explain the fallback. `--args` requires arguments (and a scope on slow
  kernels); `--no-args` skips the probe entirely.
- User recording selects automatic arguments/returns, DWARF source positions,
  call-site events and the `mono` clock. ARM64 adds `-P .` automatically; build
  the program and relevant libraries with instrumentation and debugging data.
- Mixed mode includes kernel activity outside user frames, available graph
  args/retval/retaddr, scheduler tracepoints, and real process exec/exit events.
  The default scope is architecture syscall roots, **8 kernel levels** and
  16 MiB per CPU, not every kernel function. This covers the recorded process
  timeline without claiming an unlimited call tree. Exec means image installation;
  exit is an exiting thread, not its final reclamation.
- Duplicate perf scheduling is off by default. User mode `--sched` enables that
  perf source. Mixed mode `--no-sched` omits the three scheduler tracepoints but
  keeps exec/exit, unlike the raw upstream option with the same spelling.

| Option | User/mixed behavior |
| --- | --- |
| `-o DIR` | New recording directory; existing output is never replaced |
| `--no-args` | Disable automatic user arguments/returns and mixed kernel args; retain kernel retval/retaddr |
| `-A SPEC`, `-R SPEC` | Additional native argument/return specifications for unsupported automatic ABI cases |
| `-g FUNC[,FUNC]` | Replace default kernel graph roots; `-g '.*'` deliberately selects all functions |
| `-n FUNC[,FUNC]` | Exclude kernel graphs |
| `--kernel-depth N` | Kernel depth limit, default 8; increase deliberately |
| `-b KB` | Per-CPU kernel buffer, default 16384 KiB |
| `--uftrace-bin PATH` | Optional recorder override |

## Safety boundary

Mixed mode requires root and idle tracefs with no enabled events or PID/function
filters. It refuses active sessions, holds the same lock as the kernel-only
script, and snapshots tracer, clock, options, filters, events, depth, CPU mask
and per-CPU buffer sizes. Completion, recording failures and SIGINT/SIGTERM stop
the owned process group before restoring and checking every saved setting.
Restore failures return nonzero and retain diagnostics.

`mixed.data.fgcapture/` retains `command.json`, `tracefs-before.json`,
`tracefs-after.json`, and `tracefs-restore.json`; partial captures are preserved.
SIGKILL, power loss, kernel crashes, external tracers bypassing the lock, and
already consumed trace-buffer contents cannot be recovered by this wrapper.
Large/deep captures can still lose records. Check report loss/incompleteness
warnings; exporter `complete:true` is not proof of a lossless capture.

The expert `fguftrace record ...` interface remains unchanged and does not
automatically acquire this wrapper's state protection. See the
[uftrace guide](uftrace.md) for the complete export/visualization contract.

## Preserve, share and troubleshoot

The `.data` suffix is only a naming convention: both `capture` and `mixed.data`
are valid. Select input handling by recording mode, not just the extension.

| Artifact | What to keep / how to open |
| --- | --- |
| `*.data/` | Keep the entire directory, not just a `*.dat`: symbols, maps, kernel formats and clock / argument metadata matter |
| `*.data.fgcapture/` | Keep beside the recording for the actual command and tracefs restoration readback |
| `*.jsonl` | Standalone import without uftrace; embedding source still needs matching source trees |
| Single `*.html` | Offline and directly shareable; embedded snippets need no original source tree |
| Sidecar directory | Copy the whole directory and use `fgview DIR --open` over HTTP, not just `index.html` |

For a large report, replace its output option with
`--report-mode sidecar --output report-dir`. To omit Text but retain source, add
`--no-text-view --keep-source`. Neither restricts analysis to visible rows;
sidecar is not a complete Text DOM memory bound. See the [viewer guide](viewer.md).

- Missing user functions: rebuild the application and relevant libraries with
  instrumentation and `-g`. Instrumenting an application does not instrument the
  system libc. Use expert `fguftrace` for additional library selection; the
  simplified wrapper does not forward arbitrary native `-P/-F/-T` flags.
- Missing parameter names / call sites: inspect the original JSONL for recorded
  data and specifications. User DWARF and kernel BTF are separate; a viewer update
  cannot recreate previously lost addresses, arguments or mapping paths.
- Recorder not found: check that the installed wheel is native and matches the
  architecture, not `py3-none-any`. An invalid override or incomplete bundle is an
  error, not a silent fallback to a different version.
- Mixed recording refused: investigate other tracers, enabled events and filters
  rather than clearing them blindly. After interruption, inspect
  `.fgcapture/tracefs-restore.json`; writing `nop` alone does not restore tracefs.
- Captures retain actual addresses, arguments and paths. Check suitability before
  publishing JSONL/HTML; the tool does not automatically redact them. Diagnostic
  instrumentation and recording change timings; these cases are not benchmarks.

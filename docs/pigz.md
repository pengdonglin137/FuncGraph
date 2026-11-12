# Case study: trace a gzip export with pigz and zlib

Compress a small CSV of synthetic orders with pigz v2.8 and its instrumented
zlib v1.3.2 dependency. The first run needs no root, `vmlinux` or tracefs.
Use x86-64 or ARM64 Linux with Git, GCC, Make, binutils, Python and the
[native FuncGraph wheel](uftrace.md#install-and-select-the-recorder), which
provides `fgcapture` and `fguftrace`. A patched development build can be supplied
explicitly. The [unified capture cases](capture.md) explain the three modes.

The script selects instrumentation for the host architecture. The manual `-pg`
build commands below target x86-64. On ARM64 replace `-pg` in both `flags` and
`LDFLAGS` with `-fpatchable-function-entry=2`. Direct `fguftrace record` needs
`-P .`; the unified `fgcapture` entry used by this script adds it automatically.
ARM64/GCC `-pg` clobbers the first argument,
so its saved `z_stream` pointer is not trustworthy. The maintained two-NOP entry
support needs no Capstone, but does not provide arbitrary uninstrumented dynamic
tracing; see the [architecture notes](uftrace.md#install-and-select-the-recorder).

## 1. Build and run

From the FuncGraph checkout, choose a new work directory:

The maintained uftrace patches preserve spaces in executable, library and source
paths; the script quotes those paths. If an older recorder already truncated a
saved mapping, record again with the current build: re-exporting the old capture
cannot recreate its missing path, function names, typed arguments or source.

```bash
bash samples/pigz_case.sh /tmp/fg-pigz
# Optional development override, using another new work directory:
bash samples/pigz_case.sh /tmp/fg-pigz-dev build/uftrace-x86_64/bin/uftrace
```

The script pins both source commits. To instrument an existing build manually,
rebuild **both** projects from clean trees:

| Project | Version | Pinned commit, checked by the script |
| --- | --- | --- |
| pigz | v2.8 | `fe4894f57739e3039a2ffc2a2a360d35e19bacbe` |
| zlib | v1.3.2 | `da607da739fa6047df13e66a2af6b8bec7c2a498` |

```bash
flags='-pg -g -O0 -fno-omit-frame-pointer'
(cd zlib && CFLAGS="$flags" ./configure --static && make -j2 libz.a)
make -C pigz CFLAGS="$flags -I../zlib" LDFLAGS=-pg LIBS='../zlib/libz.a -lm -pthread'
```

`-pg` enables function recording; link the executable with it as well. `-g`
keeps file/line information; `-O0` and `-fno-omit-frame-pointer` make the
debug call chain easier to follow. This is a diagnostic build, not a throughput
benchmark. Use the explicit `libz.a`, not `-lz`: the script checks that
`deflate` is defined in the binary and that there is no `libz.so` dependency.

## 2. Inspect the compression trace

The script captures `pigz -p 1 -6 -c orders.csv`, verifies that decompression
reproduces the original CSV byte-for-byte, and generates `/tmp/fg-pigz/report.html`.
Switch to **Timeline** and search for `deflate`. The observed chain goes from
`process → single_compress` in pigz through `deflate → deflate_slow → _tr_flush_block`
in zlib. The script uses `fgcapture --user` for automatic arguments, returns and
call sites. Its additional `-A`/`-R` specs record the `z_stream` pointer (hex, not
dereferenced), `flush = 4` (`Z_FINISH`), and return `1` (`Z_STREAM_END`). The
detail panel's **Call site** uses caller symbol + offset, not the argument pointer.
Text shows argument values on the entry and the result on the exit.
`-p 1` makes the example easy to follow; it does not demonstrate multi-worker behavior.
Click `pigz.c:2266`, `deflate.c:981` or `trees.c:997` in the stack to open the
embedded source excerpt, or expand the corresponding entry in Text. These
locations do not link to a kernel repository.

After building, rerun recording without downloading, compiling or specifying a
recorder path again:

```bash
cd /tmp/fg-pigz
fgcapture --user -A 'deflate@arg1/x64,arg2/i32' -R 'deflate@retval/i32' \
    -o capture-user-again -- ./pigz/pigz -p 1 -6 -c orders.csv > orders-again.csv.gz
./pigz/pigz -dc orders-again.csv.gz | cmp - orders.csv
funcgraph --no-config --uftrace capture-user-again --timeline --enable-fold --filter \
    --user-src "$PWD" --output user-again.html
```

`cmp` succeeds silently with exit 0. Use a fresh capture directory, not an overwrite
of previous evidence.

| Artifact in the work directory | Purpose |
| --- | --- |
| `pigz/`, `zlib/`, `pigz.c` | Pinned source, instrumented executable / static library, source mapping |
| `orders.csv`, `orders.csv.gz` | Input and compression output from the recorded run |
| `capture/`, `capture.fgcapture/` | Original user recording and actual invocation |
| `capture.jsonl` | Text export importable without uftrace |
| `report.html` | Offline Text + Timeline with user source |
| `dynamic.txt`, `symbols.txt` | Checks that the binary contains `deflate` and does not depend on system libz |

## 3. Follow the process from exec to exit

Do this only in an **idle experimental VM with tracefs snapshot/restore**;
see [capture safety](uftrace.md#capture-safety-and-current-limits). The following
uses the unified wrapper for snapshots, restoration and readback verification,
without manually assembling clock, argument and event options. Build on the VM
first and reuse its workspace and `orders.csv`.

```bash
cd /tmp/fg-pigz
case "$(uname -m)" in
    x86_64) syscall_prefix=__x64_sys ;;
    aarch64|arm64) syscall_prefix=__arm64_sys ;;
    *) printf 'This example covers x86_64 / ARM64 only\n' >&2; exit 1 ;;
esac
sudo "$(command -v fgcapture)" --mixed --kernel-depth 4 \
    -g "${syscall_prefix}_read,${syscall_prefix}_write,do_execveat_common,do_exit" \
    -A 'deflate@arg1/x64,arg2/i32' -R 'deflate@retval/i32' \
    -o capture-mixed -- ./pigz/pigz -p 1 -6 -c orders.csv > orders-mixed.csv.gz
./pigz/pigz -dc orders-mixed.csv.gz | cmp - orders.csv
fguftrace dump --fgraph --srcline -d capture-mixed > capture-mixed.jsonl
```

For a development build, add `--uftrace-bin /absolute/path/to/uftrace` to the
capture command and use that same executable to export. Otherwise both commands
automatically use the installed bundle.

This scope deliberately selects read / write, exec / exit and depth 4 to avoid
huge reports from a small workload. Without overrides the wrapper selects
architecture syscall roots and depth 8. Process-time coverage is not coverage
of every kernel function. `fgcapture --mixed` records available kernel
switch/waking/wakeup and exec/exit events. Its `--no-sched` omits the first three
but keeps lifecycle; raw uftrace's same-named option only disables perf scheduling.
Keep actual events in any scoped export. See
[record task switches](uftrace.md#record-task-switches) for verification and limits.

Configure **user and kernel sources separately** when generating the report.
These values identify the QEMU Linux `7.4.0-rc2` build verified for this case.
For another capture, replace the image, source commit and browser revision
together with those of the **kernel running during recording**, not the report
host's `uname -r` or the source tree's latest commit:

```bash
KERNEL_SRC=/vol_1t/Qemu/linux
VMLINUX="$KERNEL_SRC/x86_64/vmlinux"
KERNEL_REV=7475ea8b58616b33c04afb976cf0fbaad9dd63fb

funcgraph capture-mixed.jsonl --no-config --timeline --enable-fold --filter --fast \
    --user-src "$PWD" --vmlinux "$VMLINUX" --kernel-src "$KERNEL_SRC" \
    --source-rev "$KERNEL_REV" --func-links --highlight-code \
    --source-browser github --source-browser-rev "$KERNEL_REV" \
    --output mixed.html
```

- `--user-src "$PWD"` embeds pigz / zlib source, without using the kernel browser.
- `--vmlinux` resolves kernel function entries using matching DWARF debug information;
  a source directory alone cannot resolve them.
- `--kernel-src` / `--source-rev` extract kernel excerpts from a pinned source commit.
- `--func-links` makes kernel symbols clickable in Text; stack paths in Timeline link too.
- `--source-browser` / `--source-browser-rev` select kernel web links independently of
  embedded source content. This example explicitly pins GitHub links rather than
  inheriting old settings. For Elixir, use
  `--source-browser elixir --source-browser-rev v7.4-rc2`. Custom kernels need their
  own repository links, not custom line numbers pointing into Torvalds' tree.

Alternatively, store the kernel settings in a preset and supply only user sources
on subsequent runs:

```bash
fgreport init qemu-pigz --vmlinux "$VMLINUX" --kernel-src "$KERNEL_SRC" \
    --source-rev "$KERNEL_REV" --source-browser github --source-browser-rev "$KERNEL_REV"
fgreport -p qemu-pigz capture-mixed.jsonl -- --user-src "$PWD"
# Read the original directory directly with the installed recorder:
fgreport -p qemu-pigz --uftrace capture-mixed -- --user-src "$PWD"
```

Without a matching debug image, a call-flow-only report remains useful:
`funcgraph capture-mixed.jsonl --no-config --timeline --enable-fold --filter --user-src "$PWD" --output mixed-no-kernel-source.html`.
It retains mixed stacks and boundaries, but **does not generate kernel source
positions, links or excerpts**. The user-only run in the first two sections needs
no kernel source options.

In Timeline, click **Fit trace** and then **Tools ▾ → Exec / exit**. The panel links each
milestone to its original Text record. The **Markers** overlay draws ticks
on the task track. `sched_process_exec` means the new executable image was
installed, not the start of the `execve` syscall; `sched_process_exit` marks
task exit, not when the parent reaps it. No missing endpoint is inferred.

An earlier QEMU Linux `7.4.0-rc2` run with the native expert command and kernel
depth 30 took about **277 ms** from the recorded
exec event to exit, including tracing and startup/cleanup; it is not a speed
benchmark. Four read and four write roots in the main workload linked to user
calls. The archive round-tripped byte-for-byte. These historical results are not
promised timings or counts for the shallower command above.

A short capture can still produce a large report (about 89 MiB as a single HTML
file for that historical run). To open Timeline before loading Text, replace
`--output mixed.html` with `--report-mode sidecar --output mixed-report`, then run
`fgview mixed-report --open` (see the [viewer guide](viewer.md)).

Find `read` or `write` in the pigz path. The divider is over its linked kernel
root, not over `deflate`: compression runs in user space, and the I/O calls
enter the kernel separately. The output is redirected to a file, so the
recorded write path need not match a pipe or terminal. Keep the original build
tree to embed source; recorded absolute source positions are not remapped when
the capture is copied to another machine.

Keep all of `capture-mixed/` and `capture-mixed.fgcapture/` when sharing evidence.
JSONL/HTML alone cannot revalidate original raw records or restoration state.
The script leaves captures, archives and reports in the chosen directory; it
does not upload or add them to Git. Review actual addresses, arguments and paths
before publishing them.

## 4. Local reproduction (November 12, 2026)

The updated script downloads the pinned trees, builds both diagnostic components,
records userspace and generates HTML in a directory containing spaces. The same
binary also runs under the depth-4 command above in QEMU Linux `7.4.0-rc2`:

- User report: 1,108 calls. `deflate` shows `strm`, `flush=4`, return `1` and
  `single_compress+0xbff/0x1077`, with source at `pigz.c:2457`.
- Mixed report: 3,801 calls (1,108 user + 2,693 kernel), 36 matched space
  boundaries. Independent raw-page decoding agrees with all 5,409 exported
  kernel records.
- Decompression matches the input; all 81 tracefs settings read back unchanged.
  Actual exec/exit, scheduling and wakeups remain. No explicit loss or nonzero
  overrun is observed; two kernel entries lack exits and remain marked incomplete.
  This does not prove coverage of every function or a complete call tree.
- Chromium checks named arguments, returns, caller offsets, embedded user source,
  pinned kernel links and BTF parameters. Both themes at 390px touch width have
  no document-level horizontal overflow or JavaScript errors.

Local raw recordings, restoration evidence, user/mixed HTML and PNGs are retained
in `build/captures/20261112-pigz/`, not committed as large binaries or automatically
published over the online samples. See [capture acceptance](capture-validation.md).
Counts and addresses describe this run, not constants every machine must reproduce.

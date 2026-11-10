# Binary raw input

FuncGraph accepts **trace-cmd `trace.dat` containers**, detected by their magic bytes, not their filename. The container carries the event layouts, endianness, word size, clock and symbol metadata needed to decode a capture. A bare `trace_pipe_raw` page stream is **not** supported: renaming it to `.dat` does not supply this metadata. `trace-cmd report -R` field text, perf.data and Perfetto protobufs are not function_graph inputs either.

中文：[raw-input.cn.md](raw-input.cn.md).

## Quick start

On the capture machine, use trace-cmd with the function_graph tracer. For example, as root on an otherwise unused tracing setup:

```bash
trace-cmd record -p function_graph -C mono -g vfs_read \
  -e sched:sched_switch -e sched:sched_waking -o trace.dat -- cat /etc/hostname
```

Recording changes tracefs state. Do not run this example over another tracing session. Use your normal isolated-instance capture procedure when tracing is already in use; FuncGraph itself only reads the resulting file, requires no root and does not touch tracefs.

On the analysis machine, install the optional native dependencies described below, then:

```bash
funcgraph trace.dat --timeline --enable-fold --output trace.html
funcgraph trace.dat --timeline --pid 1234 --export analysis.json --output trace.html
funcgraph trace.dat --timeline --compare baseline.dat --output compare.html
funcgraph trace.dat.gz --timeline --output trace.html
cat trace.dat | funcgraph - --timeline --output trace.html
```

`--input-format auto` is the default; `raw` requires a valid trace-cmd container, and `text` rejects one rather than decoding binary bytes as replacement characters. The comparison file is auto-detected independently, so raw and text can be compared. `--raw-instance NAME` selects one named buffer instead of the default top-level buffer; it also applies to raw comparison inputs. Buffers are never silently combined. If the selected buffer has no graph/scheduling events, the error lists available named instances.

Normal compressed inputs (`.gz`, `.xz`, `.lzma`, `.bz2`) and binary stdin work. Compressed containers/stdin are spooled into a private temporary file because libtracecmd needs seekable input. Multiple inputs use the existing chronological merge with nanosecond timestamp keys. `fgreport trace.dat -- --raw-instance NAME` passes decoder options through; its line-count shortcut deliberately does not count binary newline bytes. For large raw reports use pre-filtering or `--no-text-view` explicitly.

## Optional dependencies

Text-only use needs none of these additions. For raw input, the analysis host needs:

- A native C compiler (`CC`, default `cc`) and `pkg-config`.
- Development headers and shared libraries for libtracecmd **>= 1.5.0**, libtraceevent and libtracefs.
- Runtime library discovery through the system loader or `LD_LIBRARY_PATH`.

Check discovery with:

```bash
pkg-config --modversion libtracecmd libtraceevent libtracefs
```

On the first raw input in a process, FuncGraph compiles its small `fglib/native/raw.c` bridge into a private temporary directory. A comparison reuses that process's bridge; exit removes it. Compilation never downloads dependencies or writes a persistent executable cache. The Python wheel/sdist includes this C source, not architecture-specific binaries or the upstream libraries. Use a **host** compiler, even when `CROSS_COMPILE` selects an ARM address resolver for a trace captured elsewhere.

Missing headers/compiler, unsupported data, decoder failure or a truncated container produce an actionable error before HTML publication; decoder output is not returned on failure. Existing HTML stays intact. Decoding occurs in a child process, but this is not a security sandbox: use trusted captures and maintained libraries. Trace-cmd/libtraceevent plugins are disabled when opening the file.

### Building pinned submodules

The repository pins unmodified official upstream sources:

| Submodule | Version | Commit |
|---|---|---|
| `third_party/libtraceevent` | 1.9.0 | `13701b5532e0c3295bf5670361692b0d0044228d` |
| `third_party/libtracefs` | 1.8.3 | `6fad6a14ba0d4c4b437d9e4eed7098d4bb07b4fc` |
| `third_party/trace-cmd` | trace-cmd 3.4 / libtracecmd 1.5.5 | `3ce20923b3efa60d417da7acc8a327615fbd1419` |

Each retains its own upstream licensing in `LICENSES`, `COPYING` or `COPYING.LIB`. The Python distributions do not vendor these libraries. Their submodules are a reproducible build option, not a requirement to initialize the repository for text reports. Download a Git checkout to use this option; a Python sdist does not contain the submodule sources.

Example prerequisites on Ubuntu 24.04:

```bash
sudo apt-get install build-essential pkg-config flex bison linux-libc-dev libzstd-dev zlib1g-dev
git submodule update --init --recursive
bash third_party/build.sh "$HOME/.local/funcgraph-raw"
export PKG_CONFIG_PATH="$HOME/.local/funcgraph-raw/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
export LD_LIBRARY_PATH="$HOME/.local/funcgraph-raw/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
```

The build script installs libraries/headers under that absolute prefix, does not use sudo, and suppresses upstream system `ldconfig` edits. libtraceevent 1.9 needs recent **exported** Linux UAPI headers containing `struct btf_enum64`. On older systems, install newer development headers or provide `EXTRA_CFLAGS=-I/path/to/exported/headers/include` when building. An unsanitized kernel `include/uapi` directory is not a complete userspace header set. zstd/zlib support in the built libraries controls which internally compressed containers are accepted.

The bridge reads event fields through libtraceevent, not guessed ctypes layouts. Clock validation uses libtracecmd's exported `tracecmd_get_trace_clock` accessor, which is not declared in the installed public header in the pinned version; its declaration is kept in the bridge. Updating the pinned reader requires rechecking this interface and all native tests.

## Recorded data and limits

- `funcgraph_entry`, `fgraph_retaddr_entry` and `funcgraph_exit` become ordinary entry/exit lines for the existing Text, Timeline, filtering, comparison and export pipeline. Source links still use the matching `--vmlinux`/module files. Available symbol/module ownership is preserved; missing symbols are visibly named `unresolved_0xADDRESS` rather than silently dropping calls.
- Record timestamps retain **nanoseconds**. Existing six-digit microsecond text remains unchanged. Call durations use the recorded `rettime - calltime`; this need not equal wall time when the capture excludes sleeping time. Native event layouts support both 32/64-bit words and both byte orders.
- Arguments are recorded register values, labeled `arg0=0x...`, not BTF pretty-printed C values or dereferenced strings. Both trailing and data-location arrays are decoded. Returns remain untyped hexadecimal bit patterns; absent fields do not create an args/retval capability. Use matching BTF/vmlinux when interpreting errors, pointers, signed types or void returns.
- `retaddr` is the recorded **return PC**. The caller offset uses that PC; symbol sizes come from the next kallsyms boundary, not a new measurement of code length. No architecture-specific call-instruction adjustment is invented.
- Scheduling switch/wakeup events retain task/CPU identity, state and timestamps. Switch-in context rows place resume boundaries at the actual switch timestamp, rather than at the next function event. Lost-event counts are retained; unknown counts stay unknown (`?`/JSON null). Graph overruns are shown as explicit comments.
- Supported clocks are nanosecond clocks: local, global, mono, mono_raw, boot, tai, uptime and perf; legacy files without a saved clock use the reader's local-clock convention. Counter/cycle clocks (including x86-tsc) are rejected for now; capture with `-C mono`. Local clocks still have their original cross-CPU synchronization limitations.
- Other events remain readable comments, not promises of specialized analysis. This is a complete-file decoding/normalization path, **not** live `trace_pipe_raw` reading, constant-memory processing, Text virtualization or sidecar output. Normalized Text line numbers refer to generated event rows, not binary file offsets.

The CLI's `inputs.read_lines()` path keeps the native decoder's normalized line list directly, including compressed inputs, stdin and each raw member of a mixed-input merge. It does not join that list into a full trace string and split it again. The public `inputs.read_text()` API still returns a newline-terminated string for raw inputs when explicitly requested. This removes redundant loading copies; the complete normalized trace and analysis models still reside in memory.

## Validation

The build helper fixes both `libdir_relative=lib` and the absolute `libdir` for all three upstream builds and installs. Setting only `libdir` leaves libtraceevent/libtracefs pkg-config files pointing at `lib64` on 64-bit hosts, although the libraries were installed in `lib`; a clean host can then fail to link the next library. Installation also regenerates metadata with Make's `-W` template dependency, so retrying an existing checkout repairs stale paths without deleting generated files or recompiling everything. Do not work around this by installing system copies, adding a `lib64` symlink or weakening the CI checks. CI verifies that each installed package's `pkg-config --variable=libdir` equals its private prefix's `lib` directory before running the decoder tests.

`PYTHONPATH=tests FG_RAW_REQUIRED=1 python3 -m unittest test_raw -v` requires the native dependencies rather than skipping them. Synthetic offline containers cover both byte orders/word sizes, sub-microsecond durations, arguments, returns, retaddr, module ownership, scheduling, event loss, invalid clocks/fields and truncated files. CLI checks exercise comparison/export; installed-package smoke tests accept `--with-raw`. CI builds the pinned libraries on Ubuntu 24.04 and checks both wheel and sdist before packaging/release proceeds.

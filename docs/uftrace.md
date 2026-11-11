# User / kernel tracing with uftrace

## Implementation status (2026-11-11)

The optional native capture/export foundation is implemented and validated on
x86_64 QEMU Linux **7.4.0-rc2** and Raspberry Pi 4 Linux
**6.18.56-v8-MY_CUSTOM_KERNEL+**. FuncGraph imports **user-only, kernel-only and
mixed** recordings without a required `vmlinux` or native Python dependency.
Existing ftrace text and trace-cmd inputs still work. The state-preserving
`fgcapture.sh --user` wrapper is not implemented.

For a real application and an instrumented third-party library, see the
[pigz + zlib compression case](pigz.md).

## Mixed sample: follow `sendto` into the kernel

Open [sample_mixed.html](../sample_mixed.html) in your browser, or use the
[GitHub Pages sample](https://pengdonglin137.github.io/FuncGraph/sample_mixed.html).
Switch to **Timeline** with default **Context** colors. Follow
`main → run_round → exchange_loopback → send_frame → dispatch_datagram → sendto → __x64_sys_sendto → … → loopback_xmit`.
Select `dispatch_datagram` to see its recorded function address, the five
arguments (including the 512-byte length), and return value `512`. Click
`demo_mixed.c:33` on the `send_frame` stack row for the recorded call site;
`dispatch_datagram` retains its separate function entry at line 25. The two `receive_frame` calls return
`512` and `-1`; the latter uses `MSG_DONTWAIT` on an empty queue. Its user return
is not the raw kernel return value.

Select `__x64_sys_sendto` to see the boundary at the same point in the call
stack and as a thin line over the recorded kernel interval. Text shows an
underlined `K` on that entry, arguments and addresses on user entry rows, and
returns on user exit rows. Use `space:user` or `space:kernel` to inspect one space.

The [checked-in JSONL](../samples/sample_mixed.jsonl) is a scoped extract from
the x86_64 QEMU Linux `7.4.0-rc2` capture of [`demo_mixed.c`](../samples/demo_mixed.c)
on November 11, 2026, with `--auto-args --srcline -T '.@callsite'`. It contains 25 user calls,
689 kernel calls, 25 recorded user call-site events and five linked transitions.
The extract keeps the original call records and call-site events (including
addresses, parameters and raw return values), but not scheduler/perf events.
Sixteen user calls have decoded arguments and nineteen have decoded returns,
covering `main`, `run_round`, file transfer and the multi-layer UDP path.
The published report uses the matching kernel debug image: 306 unique kernel
functions retain entry locations in the call stack, and 465 unique captured
kernel call sites resolve to source, including 282 inline chains. Links are pinned
to Linux `v7.4-rc2`. Select `__x64_sys_sendto` for its `net/socket.c:2286` stack
entry and **Call site source** in `do_syscall_x64`, `arch/x86/entry/syscall_64.c:61`.
The recorded PC `0xffffffff82129117` is retained separately from
`do_syscall_64+0xb7/0x580`. Expanding its Text row shows the same call-site chain.
The matching BTF also verifies the nonblocking receive error path and supplies
safe argument-name prefixes for 280 kernel functions and typed values for 292 functions. Select `__sys_sendto` for
`fd`, `buff`, `len`, `flags`, `addr` and `addr_len`; expand **Raw argument slots**
to inspect the unchanged recorded values. Select each `receive_frame` call to see
its distinct caller line, `demo_mixed.c:47` or `demo_mixed.c:49`, rather than its
shared entry at line 36. The caller return PC is retained separately from the
recorded callee address, alongside the explicit **call site address** (`PC - 1`).
For `__x64_sys_sendto`, **args (BTF)** shows its single `pt_regs` pointer, not six
invented arguments; the unchanged register slots remain expandable.
Missing libc source stays unavailable, not kernel-linked.
The complete native export was independently checked against 112,287 saved kernel
records, and all 75 snapshotted tracefs settings were restored exactly. Viewing the
HTML needs neither the kernel tree nor uftrace. To rebuild it from the repository
root, provide the matching debug-enabled image and source repository:

```bash
python3 funcgraph.py --no-config --timeline --enable-fold --filter \
    --user-src samples --report-mode single --func-links --fast --highlight-code \
    --vmlinux /vol_1t/Qemu/linux/x86_64/vmlinux --kernel-src /vol_1t/Qemu/linux \
    --source-rev 7475ea8b58616b33c04afb976cf0fbaad9dd63fb \
    --source-browser github --source-browser-rev v7.4-rc2 \
    --output sample_mixed.html samples/sample_mixed.jsonl
FG_CONFIG=/dev/null python3 samples/make_shots.py --mixed-only  # needs Playwright + Chromium
```

Adjust the local paths to your matching build. Without that image, the following
still rebuilds the same recorded calls, timings, boundaries and user source,
but cannot resolve kernel source or perform the image's BTF checks:

```bash
python3 funcgraph.py --no-config --timeline --enable-fold --filter \
    --user-src samples --report-mode single --highlight-code \
    --output mixed-no-kernel-source.html samples/sample_mixed.jsonl
```

## Install and select the recorder

The release's Linux x86_64 / aarch64 wheels include the patched recorder and
its five libmcount recording libraries. Install the wheel matching your machine;
`py3-none-any` and the Python source distribution do not contain native binaries.
The platform wheels are built on Ubuntu 22.04, not tagged as manylinux. On Ubuntu,
install the runtime prerequisites before using the recorder:

```bash
sudo apt-get install libtraceevent1 libelf1 libdw1 liblzma5 libstdc++6
python3 -m pip install './funcgraph-<version>-py3-none-linux_x86_64.whl[all]'
fguftrace --version
```

Use `linux_aarch64` on ARM64. `fguftrace record ...` and `fguftrace dump ...`
run the installed recorder with a relocated libmcount path automatically.
`funcgraph --uftrace DIR` and `fgreport --uftrace DIR` choose an explicit
`--uftrace-bin PATH` first, then the bundled recorder, then patched `uftrace`
on `PATH`. An invalid override or incomplete bundle is an error, not a silent
fallback. Upstream uftrace without the maintained patches cannot export `--fgraph`.
Reading saved JSONL needs none of these native runtime libraries or executables.
`fgcapture.sh` remains the kernel-only tracefs recorder; it does not accept
uftrace user/mixed recording options.

Spaces in workload executable/shared-library paths, recording directories and
user source paths are supported; quote their shell arguments. Captured mapping
names retain internal tabs and trailing spaces too. Export uses saved symbols
and source metadata even after the original executable/library is moved away.
Older recordings whose mapping paths were already truncated must be recorded
again; neither re-export nor HTML generation can recover missing capture data.

**ARM64 argument recording:** Raspberry Pi GCC 14.2.0 with `-pg` overwrites `x0`
before `_mcount`; that mode's `arg1` is not the original first argument. Do not
guess missing values from old recordings. Rebuild every traced object and library
with `-fpatchable-function-entry=2` instead of `-pg`, and record with `-P .`.
Both NOPs must be at the function entry; do not use a nonzero second compiler
argument. The maintained patch enables these entries without Capstone and refuses
to rewrite ordinary instructions: it does not enable arbitrary uninstrumented
dynamic tracing. All 24 native export regressions pass on the Pi, including the
first argument, eight integer arguments, PIE/non-PIE, `-O0`/`-O2`, recursion and
shared-library paths.

```bash
cc -fpatchable-function-entry=2 -g -O0 -fno-omit-frame-pointer \
    -o demo_mixed samples/demo_mixed.c
fguftrace record -P . --no-sched --srcline -T '.@callsite' -d user.data \
    ./demo_mixed demo-input.txt demo-output.txt
```

Prepare the input/output files as in the README. Rebuild shared libraries with
the same instrumentation and add `-P '.@libexample.so'` for each selected library;
`-P .` alone does not select every library. Retain `-P .` for mixed recording and
select kernel functions for the actual architecture. The QEMU examples below
target x86_64: do not copy their `__x64_sys_*` filters to ARM64.

## Generate a report

```bash
funcgraph --uftrace CAPTURE \
    --timeline --enable-fold --filter --user-src samples --output mixed.html
fgreport -P --uftrace CAPTURE -- --user-src samples
# Or import a saved export, with no uftrace executable needed:
funcgraph capture.jsonl --timeline --enable-fold --filter --output mixed.html
```

The directory reader runs the selected patched executable with
`dump --fgraph --srcline`. For a development build, optionally add
`--uftrace-bin build/uftrace-x86_64/bin/uftrace`. JSONL is detected from the stream
header; `--input-format uftrace` selects it explicitly, including for stdin
and compressed input. Use one recording or JSONL per report. `--compare BASE`
accepts JSONL or a legacy trace file; Compare and `--export` group user and
kernel names separately, even when their symbols have identical spelling.

- **Lifecycle milestones:** when `sched_process_exec` / `sched_process_exit` records exist,
  Timeline's **Exec / exit** panel lists every recorded event in pages of at most 100 rows.
  **Previous page** / **Next page** replace the rows rather than accumulating DOM nodes.
  Click a **Trace time** to center its recorded position at the current zoom, within report
  bounds, without requiring Text, changing filters or inventing a function selection.
  The list is unfiltered; a hidden kernel track stays hidden when locating its event.
  **Export CSV** includes all pages, task identities, trace lines, relative nanoseconds and
  recording-clock status. Absolute `timestamp_ns` adds a safe integer offset to the string
  origin without floating-point conversion; an unsafe offset leaves this field empty.
  Exec means the new image is installed; exit means a task is exiting, not its final reap.
  Missing milestones are not inferred. Unverified user/kernel clocks remain separate and
  CSV explicitly records `clock_aligned=false`. Track markers visit only the visible range
  and coalesce identical pixels per track without removing any original event.
  Pagination bounds panel DOM, not total report or CSV-export memory; all events and their
  index remain in memory. See the [lifecycle performance record](performance-lifecycle.md)
  for the reproducible synthetic benchmark and its measurement limits.
- **Events without calls:** timestamped scheduling observations or uftrace exec /
  exit milestones can produce a Timeline with zero function records, including
  single / sidecar / auto delivery and `--no-text-view`. For example,
  `funcgraph events.jsonl --timeline --no-text-view --output events.html` needs no
  vmlinux or source tree for these events. A visible notice identifies the lack of
  recorded function calls; Tasks, wait chains and event CSV remain available.
  A single milestone is only an observed point, not an invented call, CPU duration
  or process lifetime. Search has no calls to match, **Hide unmatched** is disabled,
  and shared filters affect Text lines only, leaving event context unfiltered.
  Empty, loss-only, unknown-event-only and untimed input do not invent a Timeline.
  Kernel scheduler events still count as kernel observations when no kernel calls
  exist, so unverified user/kernel clocks keep task identities separate.
- **Mixed:** Timeline's default Context colors distinguish user code from
  kernel execution. The thin line along a linked kernel root's top edge spans
  exactly that recorded interval, at the depth where it occurred. Text uses
  `U` / `K` and underlines the boundary tag; the call stack separates the two
  spaces at the same transition. Single-space reports omit the divider.
- **Kernel context:** joining the call stacks keeps the recorded kernel flags
  on kernel frames only. Timeline details use the entry's IRQ/BH, hardirq,
  softirq, NMI, reschedule, preemption and migration state. Text filters each
  record independently, e.g. `space:kernel AND irqs:d`. Missing context is
  not inferred. The meaning of bit `0x02` depends on the saved kernel release.
- **Matching:** pair native entry/exit by thread, address space, depth and
  address, then link a complete kernel root only inside a recorded user call
  of the same thread with a verified shared clock. This establishes temporal
  containment, not the exact user instruction. It uses record timestamps, not
  graph-internal counters. Unmatched calls remain visible; conflicting roots
  use an unjoined lane. Missing or incompatible clock readbacks leave the
  spaces separate with a warning; do not compare their times.
- **Sources:** recorded user file:line positions are shown independently of
  kernel source resolution. `--user-src DIR` embeds excerpts only for paths
  that resolve within that root; click the source position in the stack or
  expand its Text row. Missing and out-of-root files remain plain locations.
  User frames never use the kernel `--base-url`, even for identical names. Use
  `--no-text-view --keep-source` to keep excerpts without mounting Text.
  For kernel functions, add `--vmlinux`, `--kernel-src` and `--func-links` to resolve
  entry locations and embed source excerpts in Text and Timeline. The selected
  `--source-rev` / `--source-url` provider also supplies these excerpts. Source
  links use the kernel browser and revision. A resolved captured call site takes
  precedence over the function-entry excerpt; the latter remains a labeled
  fallback. Missing debug positions or files leave the source unavailable rather
  than borrowing a user location.
- **Kernel call sites:** updated native exports add optional `retaddr_symbol`
  metadata from the recording's saved `kallsyms`, never the analysis host's live
  table or a guessed KASLR slide. Text and Timeline display
  `function+0xOFFSET/0xRANGE [module]`; the original return PC remains in JSONL and
  report metadata and is shown separately in Timeline. The range ends at the next
  distinct saved symbol, not a measured ELF function size. Lookup uses `PC - 1`
  to choose the preceding function at an exact boundary, but keeps the original
  return offset; the existing source resolver subtracts one exactly once.
  Module callers retain their module identity and require the matching module
  image. Unknown, redacted, non-code or unbounded symbols stay raw. Older raw-only
  JSONL is still supported; re-export its original capture with the updated patched
  executable to obtain symbol metadata. Supplying only an arbitrary vmlinux does
  not authorize guessing raw addresses.
- **User call sites:** `recorded address` identifies the callee, not the instruction
  that called it. Add `-T '.@callsite' --srcline` when recording, for example:

  ```bash
  fguftrace record --srcline -T '.@callsite' -d capture.data ./app
  funcgraph --uftrace capture.data --user-src "$PWD" --timeline --enable-fold --output report.html
  ```

  The updated patched exporter preserves the event bytes and return PC, resolving
  `PC - 1` with the recorded session's module maps and matching debug images.
  Details show this exact value as **call site address**, keeping the original
  PC as **caller return address** and the callee entry as **recorded address**.
  These distinct addresses are never substituted for one another; `PC - 1`
  identifies a position inside the call, not necessarily its instruction start.
  An event is paired only with the next user record of the same thread when that
  record is an entry exactly one ns later. Loss or mismatches never invent a site.
  Text and details prefer **User call site source**. A stack's parent row uses
  the child's site only when the captured caller symbol matches that parent.
  Otherwise the explicitly labeled function-entry fallback and original addresses
  remain. Re-record old captures without call-site events: regenerating HTML cannot
  recover a caller PC that was never recorded.
- **Kernel argument counts and widths:** matching `--vmlinux` or `--btf` trims
  **args (BTF)** and Text to a trusted prototype, displaying `name=typed_value`.
  Integers are masked to their declared BTF bit width, then interpreted with the
  correct signedness: an `int` slot of `0xdeadbeefffffffea` becomes `-22`, while
  an `unsigned char` becomes `0xea`. Pointers retain exact hexadecimal values
  at the known target word size, without dereferencing or JavaScript floating-point
  conversion. Zero-parameter functions hide spare slots; missing captured slots
  use `…`, never invented values. **return (BTF)** applies the same width/sign
  correction, boolean returns use bit 0 as `true/false`, and `void` has no value.
  Expand **Raw argument slots** or **Original return bits** for unmodified
  register values; the JSONL and model retain their original data.
  Only an ABI-safe integer/pointer prefix is interpreted. Aggregates, floats,
  variadic tails, conflicting prototypes, compiler clones and unknown pointer
  word sizes never force a count/slot mapping. Remaining slots use `slotN`.
  No additional DWARF lookup is needed. Without matching BTF, **raw args** and
  **raw return** remain; byte-for-byte equivalence with every kernel version's
  `cat trace` formatter is not claimed.
- **BTF reader dependencies:** the report-side `fglib/btf.py` uses only the Python
  standard library; neither libbpf nor bpftool is required. It reads matching raw
  BTF, ELF `.BTF` sections and module split BTF with its base, extracting the
  prototypes and scalar types needed here. It is not a complete libbpf replacement
  and does not load eBPF programs. Native recorder build dependencies are separate;
  see the reproducible build section below.
- **User arguments:** compile with debug information and record with `--auto-args`,
  or specify `-A` / `-R` explicitly. For example,
  `fguftrace record --auto-args --srcline -T '.@callsite' -d capture.data ./app`.
  Automatic scalar values follow uftrace replay's number formatting. Unsupported
  strings, floats, enums and aggregates retain their bytes; absent specifications
  are not guessed. Kernel BTF never trims or names user parameters. Old captures
  without argument payloads must be recorded again.
- **Filtering:** with `--filter`, use `space:user` or `space:kernel` in Text
  or Timeline's shared Filter. Sidecar reports can apply this filter before
  Text loads. CLI tree pre-filters are not supported on uftrace streams yet.
- **Uncertainty:** missing entries/exits and losses are marked, not filled in
  as measured calls. CPU for user calls remains unknown. User values appear
  when uftrace recorded supported scalar argument/return specifications;
  otherwise recorded bytes are labeled as raw bytes, not guessed. The
  recorded address is not a resolved source address. Kernel returns use matching
  BTF types when available while retaining raw bits, with errno analysis
  only for known BTF types. Kernel scheduler tracepoints drive scheduling
  analysis; perf events do not produce duplicate intervals. The JSONL retains
  native fields not projected into the report.

## Reproducible native build

The upstream base is locked to `842ed731248a9daded38774d8fb26c7b09474461`
(v0.20). [`third_party/versions.json`](../third_party/versions.json) locks this and
the three raw-decoder repositories to their current full commit SHAs. The build
checks every gitlink against this lock and requires a clean uftrace checkout at
the selected commit before applying patches. Do not use `git submodule update --remote`.
See [dependency pins](../third_party/README.md) before intentionally upgrading.

Building or updating the native payload requires a Git checkout, not an installed
Python wheel/sdist. Installing a native release wheel does not require a compiler.

```bash
git submodule update --init third_party/uftrace
scripts/uftrace.sh check
JOBS=4 scripts/uftrace.sh build
build/uftrace-x86_64/bin/uftrace --version

CROSS_COMPILE=aarch64-linux-gnu- scripts/uftrace.sh build --arch arm64
```

Build prerequisites: compiler/make/pkg-config and libtraceevent development
headers/libraries for kernel decoding; libelf/libdw development packages enable
user DWARF source lines. Optional upstream features depend on installed libraries.
Cross builds need matching **target** libraries and pkg-config settings, not just
a cross compiler. `arm64` maps to upstream's `aarch64`; output paths use the latter.

The script verifies the indexed submodule pin, archives pristine source, applies
each patch in order, and builds into `build/uftrace-src-ARCH` and
`build/uftrace-ARCH`. It refuses to overwrite either directory. Move/remove them
explicitly before rebuilding. It isolates Git discovery so the outer FuncGraph
repository cannot supply uftrace's version. The install prefix contains the pin
and SHA-256 patch manifest. No system installation or ldconfig changes are made.

To change patches:

```bash
git -C third_party/uftrace worktree add --detach "$PWD/build/uftrace-dev" HEAD
# Apply the existing series with git am in this worktree before editing.
# Commit each logical change, then regenerate and verify the entire series:
scripts/uftrace.sh refresh
scripts/uftrace.sh check
```

`refresh` requires a clean, linear worktree descended from the pin. It validates
the new series against an independent pristine archive before replacing the old
series. Upstream submission/removal conditions are in `patches/uftrace/README.md`.

To build a release payload, set `FG_UFTRACE_RELEASE=1` when running the build
script; this disables optional Python/Lua, curses, unwind and Capstone features,
but retains ARM64 two-NOP entry recording, reported as `patchable` rather than
general `dynamic` support in the version string.
Set `FG_UFTRACE_PREFIX="$PWD/build/uftrace-x86_64"` when building a platform wheel
(use `aarch64` on ARM64). The wheel carries the executable, all five recording
libraries, GPL-2.0 `COPYING`, the fixed base/patch hashes and a deterministic,
fully patched `source.tar.gz`. It does not bundle the OS shared libraries.
The release also attaches that corresponding source archive separately.
This does not select a project-wide license. Full commands and relocation tests
are in the [release checklist](releasing.md).

## Native export contract

```bash
fguftrace dump --fgraph --srcline -d CAPTURE > capture.jsonl
# Development equivalent: build/uftrace-x86_64/bin/uftrace dump --fgraph --srcline -d CAPTURE
```

This exports JSONL, **not ftrace text**. The first record identifies
`schema=funcgraph.uftrace.v1`, integer nanosecond timestamps,
`order=streams`, and `depth_basis=address-space`. Each user task and kernel CPU
retains its recorded ordering/native depth. The importer merges by
integer timestamp, preserving tie order, and join kernel depth below the active
user frame of the same task. It must leave kernel activity outside recorded user
frames as roots. There is no implicit clock adjustment or synthetic completion.

- Function records: `type=entry|exit`, `space=user|kernel`, `time_ns`, `tid`,
  `cpu`, `depth`, hexadecimal `address`, `name`; user records also carry recorded
  `pid`/`comm` and available `source={file,line}`. Unknown user CPU is `null`.
- Kernel context: `common_flags`, `common_preempt_count`; optional hexadecimal
  `retaddr`, `retval`, `args` array, integer `calltime_ns`, `rettime_ns`, `overrun`.
  Missing fields are omitted; empty arrays are distinct from missing arrays.
  Flexible/fixed/dynamic/relative arrays use recorded format bounds and byte order.
  Malformed arrays fail the export rather than silently becoming absent.
  Graph-internal call/return counters need not share the record timestamp's clock
  origin; do not use them to sort or align mixed records. `time_ns` is the record
  timestamp; the two native counters remain separate evidence.
- `retval` is **untyped raw bits**, not a proved errno, signed integer or pointer.
  Field presence does not prove that its tracing option was enabled: consult
  `kernel_settings` readbacks. No BTF type/provenance is invented.
- Kernel tracepoint `event` records retain the original **common PID**, name,
  formatted payload and flags, including scheduling events from other tasks.
  User event/argument payloads, when present, retain their `payload_hex` bytes.
  Supported automatic, signed/unsigned integer and hex/pointer specifications (8, 16, 32,
  or 64 bits) add optional `user_args` (entry list of strings) or `user_retval`
  (exit string). Strings avoid losing 64-bit precision in browsers. An unsupported
  or incomplete specification leaves the whole payload raw; older exports still work.
- Perf `event` records have `origin=perf`, `space=null`, unknown `cpu=null`, an
  opaque stream index and the available scheduling/task/comm fields. Do not count
  perf scheduling and equivalent kernel tracepoints twice in an importer.
- `lost` records preserve the loss count. Kernel page loss belongs to a CPU,
  with `tid=null`; it is not falsely attributed to the next task on that CPU.
- `clock` comes from `funcgraph-clock`, written by new captures. Old captures
  lacking it export `null`. `kernel_settings` comes only from saved
  `kernel-funcgraph.info`, never from the machine doing the export. Its
  `trace_clock` lists the actual selected kernel clock in brackets.
- The last record is `{"type":"end","complete":true}` for a completed export.
  Missing/false completion must not be accepted as a complete capture. Kernel
  decode errors exit unsuccessfully. Capture loss is still loss even when all
saved bytes were successfully exported.

## Capture safety and current limits

`uftrace record --kernel-full-info` enables available graph options and
`sched_switch`, `sched_waking`, `sched_wakeup`; absent capabilities are reported
as unavailable, not simulated. The current upstream reset code restores defaults,
**not the prior tracefs configuration**. Until the state-preserving wrapper is
implemented, run kernel recording only in an idle, dedicated experimental
environment with explicit settings snapshot/restore. Do not use it against an
active tracing session. Trace buffer contents cannot be restored by restoring
settings.

Use a `-pg -g -O0 -fno-omit-frame-pointer` dynamic build of `samples/demo_io.c`,
private input/output files, a shared `--clock=mono`, a suitable kernel buffer
and architecture-specific syscall graph roots. Wide/deep unfiltered captures
may overflow. Source files for Pi experiments were copied from this repository;
the old `/vol_1t/raspi/linux` path does not exist on the currently reachable Pi.

For the mixed demo, build `samples/demo_mixed.c` with the same flags. In the
idle tracefs validation environment, pass separate exact syscall roots and
explicit `-A`/`-R` specifications, for example:

```bash
uftrace record -K 30 --clock=mono --kernel-full-info --no-sched --auto-args --srcline -T '.@callsite' -d CAPTURE \
    -F '__x64_sys_read@kernel;__x64_sys_write@kernel;__x64_sys_sendto@kernel;__x64_sys_recvfrom@kernel' \
    -A 'dispatch_datagram@arg1/i32,arg2/x64,arg3/u64,arg4/x64,arg5/u32' \
    -A 'receive_frame@arg1/i32,arg2/x64,arg3/u64,arg4/i32' \
    -R '^(read_block|write_block|send_frame|dispatch_datagram|receive_frame)$@retval/i64' \
    -R '^(transfer_file|exchange_loopback|run_round)$@retval/i32' \
    ./demo_mixed INPUT OUTPUT
```

QEMU 7.4 exposes args and retaddr together. This must not be extrapolated to
older kernels: the Pi 6.18 record layout/capabilities are validated separately.
Native depth and CPU are preserved; no bounded-memory/global-sorting claim is
made for the exporter or importer.

## Validation

```bash
FG_UFTRACE_REQUIRED=1 PYTHONPATH=tests python3 -m unittest \
    test_uftrace_build test_uftrace_export -v
python3 tests/verify_uftrace_records.py CAPTURE capture.jsonl
```

The hermetic tests need no root or tracefs. They record an instrumented local
user workload and inject independently generated kernel pages for both byte
orders, 32/64-bit words, entry/retaddr formats, fixed/flexible/dynamic/relative
arguments, absent return values, sparse CPUs, common PID, malformed arrays,
unknown events and explicit losses. CI builds the pinned patched release and
requires these tests; the Python-only suite can skip the optional executable.

The offline verifier independently decodes the saved ring pages and compares
every exported kernel record's timestamps, CPU/PID, type, native depth, full
addresses, flags, preempt count and optional raw fields. It does **not** compare
against `cat trace` after uftrace drained the live buffer; that would not be a
same-run reference. Experimental snapshots verify every saved setting after
completed captures, separately from byte-decoder verification; the production
wrapper's failure/cancellation recovery is still pending.

### Local native package acceptance (2026-11-11)

- The complete x86_64 native/browser suite runs 673 tests: 670 pass and three
  optional cases skip.
- Both x86_64 and Raspberry Pi ARM64 pass all 24 native export regressions.
  ARM64 is built cleanly from the corresponding source and also passes two
  two-NOP safety cases and two mapping-path unit tests, without relaxing argument assertions.
- Both platform wheels pass Twine and base/`[all]` installation checks outside
  the checkout, with the original native build prefix moved away for each run.
  The extras run passes 12 of 13 tests, skipping only the unrequested raw-install
  integration. The Pi has no `gawk`; source snippets and tail-call assertions pass.
- The wheels carry byte-identical nine-patch native source archives. All four
  upstream gitlinks and locks remain unchanged; 36 independent dependency,
  build and runtime tests pass on each architecture.
- After isolating listed-source test fixtures from host `gawk` availability,
  92 affected source/browser regressions and 18 additional Pi source regressions
  pass. The real no-`gawk` installation check does not use that fixture.
- The installed ARM64 recorder completes the user-only pigz/zlib workload with
  1,108 calls: `deflate` retains its nonzero original pointer and `4` arguments,
  returns `1`, and gzip round-tripping matches byte-for-byte. An offline browser
  verifies the details and clickable `deflate.c:981` source without extra requests
  or JavaScript errors.

These are local build/installation results, not evidence of a push, successful
GitHub Actions run or published Release. This ARM64 pigz recording is user-only;
it does not replace the real kernel capture/byte comparisons below.

### Real capture evidence (2026-11-11)

| Environment | Kernel records independently matched | Context | Restored settings |
| --- | ---: | --- | ---: |
| QEMU, x86_64, 7.4.0-rc2 | 79,118: 39,539 entries + 39,539 exits + 40 events | args + retaddr + retval | 74 / 74 |
| Pi 4, aarch64, 6.18.56-v8-MY_CUSTOM_KERNEL+ | 317,693: 158,761 entries + 158,695 exits + 237 events | retaddr + retval; args absent from the actual records | 68 / 68 |

Both captures use `mono`, contain 15 user entries/exits each, and resolve `main`
to `demo_io.c:22`. The ten syscall root entry/exit records inside `main` lie
inside their matching user `read`, `write`, `sendto`, and `recvfrom` wrappers
without clock correction. Scheduling/perf events are also preserved. No explicit
page-loss or nonzero graph-overrun record was observed in these two scoped runs;
this is not a guarantee about wide/unfiltered captures.

The Pi raw stream contains 66 more kernel entries than exits, and a naive
single-stack check also encounters interrupt-context discontinuities. Byte-level
agreement is **not proof of a complete call tree**: the importer must retain
incompleteness/interrupt distinctions instead of synthesizing measured returns.
The old kernel reports args enabled even though retaddr-format records contain
no arguments. Actual records, not requested/read-back options alone, determine
which data can be shown.

Validation also caught an upstream kallsyms bug: lower-address module groups
arriving after core symbols produced underflowed function ranges, falsely naming
`__arm64_sys_read` as `vc4_cec_irq_handler_rx_bare`. Patch 0004 recomputes ranges
after sorting. A regression fails on the pre-fix build and passes after the fix.
Raw addresses remain unchanged and authoritative.

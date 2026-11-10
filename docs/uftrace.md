# User / kernel tracing with uftrace

## Implementation status (2026-11-11)

The optional native capture/export foundation is implemented and validated on
x86_64 QEMU Linux **7.4.0-rc2** and Raspberry Pi 4 Linux
**6.18.56-v8-MY_CUSTOM_KERNEL+**. FuncGraph imports **user-only, kernel-only and
mixed** recordings without a required `vmlinux` or native Python dependency.
Existing ftrace text and trace-cmd inputs still work. The state-preserving
`fgcapture.sh --user` wrapper is not implemented.

## Mixed sample: follow `sendto` into the kernel

Open [sample_mixed.html](../sample_mixed.html) in your browser, or use the
[GitHub Pages sample](https://pengdonglin137.github.io/FuncGraph/sample_mixed.html).
Switch to **Timeline** (default **Context** colors) and select
`__x64_sys_sendto`. Follow `main → sendto → __x64_sys_sendto → … → loopback_xmit`.
The thin line over `__x64_sys_sendto` marks the boundary for that call only.
The call stack separates user and kernel frames at the same point; Text uses
an underlined `K` on the kernel entry. In the call stack, click `demo_io.c:22`
to view the embedded source. Use the shared Filter with `space:user` or
`space:kernel` to inspect either address space.

The [checked-in JSONL](../samples/sample_mixed.jsonl) is a scoped extract from
the x86_64 QEMU Linux `7.4.0-rc2` capture of [`demo_io.c`](../samples/demo_io.c)
on November 11, 2026. It contains 15 user calls, 916 kernel calls and five
linked transitions. The extract keeps the original call records (including
addresses, parameters and raw return values), but not scheduler/perf events.
No kernel debug image is used here; use the [kernel-only sample](../sample.html)
for resolved kernel source and BTF-backed error paths. To rebuild the mixed
report from the repository root (no uftrace executable or `vmlinux` needed):

```bash
python3 funcgraph.py --no-config --timeline --enable-fold --filter \
    --user-src samples --report-mode single \
    --output sample_mixed.html samples/sample_mixed.jsonl
FG_CONFIG=/dev/null python3 samples/make_shots.py  # screenshots; needs Playwright + Chromium
```

## Generate a report

```bash
funcgraph --uftrace CAPTURE --uftrace-bin build/uftrace-x86_64/bin/uftrace \
    --timeline --enable-fold --filter --user-src samples --output mixed.html
# Or import a saved export, with no uftrace executable needed:
funcgraph capture.jsonl --timeline --enable-fold --filter --output mixed.html
```

The directory reader runs the patched executable (`uftrace` on `PATH` by
default) with `dump --fgraph --srcline`. JSONL is detected from the stream
header; `--input-format uftrace` selects it explicitly, including for stdin
and compressed input. Use one recording or JSONL per report. `--compare BASE`
accepts JSONL or a legacy trace file; Compare and `--export` group user and
kernel names separately, even when their symbols have identical spelling.

- **Mixed:** Timeline's default Context colors distinguish user code from
  kernel execution. The thin line along a linked kernel root's top edge spans
  exactly that recorded interval, at the depth where it occurred. Text uses
  `U` / `K` and underlines the boundary tag; the call stack separates the two
  spaces at the same transition. Single-space reports omit the divider.
- **Matching:** pair native entry/exit by thread, address space, depth and
  address, then link a complete kernel root only inside a recorded user call
  of the same thread with a verified shared clock. This establishes temporal
  containment, not the exact user instruction. It uses record timestamps, not
  graph-internal counters. Unmatched calls remain visible; conflicting roots
  use an unjoined lane. Missing or incompatible clock readbacks leave the
  spaces separate with a warning; do not compare their times.
- **Sources:** recorded user file:line positions are shown independently of
  kernel source resolution. `--user-src DIR` embeds small excerpts for files
  under that root; click the source position in the stack or expand its Text
  row. No user locations are routed to a kernel repository. Use
  `--no-text-view --keep-source` to keep excerpts without mounting Text.
- **Filtering:** with `--filter`, use `space:user` or `space:kernel` in Text
  or Timeline's shared Filter. Sidecar reports can apply this filter before
  Text loads. CLI tree pre-filters are not supported on uftrace streams yet.
- **Uncertainty:** missing entries/exits and losses are marked, not filled in
  as measured calls. CPU for user calls remains unknown; raw user payloads
  are not decoded. Kernel returns are shown as raw bits, with errno analysis
  only for known BTF types. Kernel scheduler tracepoints drive scheduling
  analysis; perf events do not produce duplicate intervals. The JSONL retains
  native fields not projected into the report.

## Reproducible native build

Build from a Git checkout, not from an installed Python wheel/sdist.

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

## Native export contract

```bash
build/uftrace-x86_64/bin/uftrace dump --fgraph --srcline -d CAPTURE > capture.jsonl
# Equivalent: uftrace dump --format=fgraph --srcline -d CAPTURE
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

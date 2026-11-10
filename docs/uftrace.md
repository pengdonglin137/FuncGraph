# User / kernel tracing with uftrace

## Implementation status (2026-11-11)

The optional native capture/export foundation is implemented and validated on
x86_64 QEMU Linux **7.4.0-rc2** and Raspberry Pi 4 Linux
**6.18.56-v8-MY_CUSTOM_KERNEL+**. The planned `funcgraph --uftrace`, mixed
visualization, user sources and `fgcapture.sh --user` are **not available yet**.
Existing kernel text/raw input remains unchanged. Do not pass JSONL to the
function-graph text parser.

The visualization contract includes **user-only, kernel-only and mixed** inputs.
Function address space must be explicit, never inferred from its name. Mixed
views will mark real user→kernel subtree boundaries with a subtle, legible line,
an address-space label and a legend. The boundary follows each subtree's actual
time span/depth, not a fixed horizontal track division. Single-space reports
must not invent missing frames, require unrelated source files, or draw redundant
boundaries. This is an acceptance requirement, not a delivered UI feature.

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
retains its recorded ordering/native depth. The future importer must merge by
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
made for the exporter or future importer.

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

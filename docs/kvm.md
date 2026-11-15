# Host QEMU / KVM in the existing Timeline

中文：[kvm.cn.md](kvm.cn.md).

FuncGraph displays Host KVM tracepoints in its normal **Timeline**, without a
separate viewer or mandatory `function_graph` records. Events are not counted as
function calls: an event-only report correctly has **0 calls**.

## Open a recording

Install the optional [raw dependencies](raw-input.md), then run:

```bash
funcgraph --no-config host.dat --timeline --enable-fold --filter --output host.html
```

For events in a named tracefs instance, select that buffer:

```bash
funcgraph --no-config host.dat --raw-instance funcgraph-kvm \
  --timeline --enable-fold --filter --export host-analysis.json --output host.html
```

Buffers are not automatically combined. For a large report, use
`--report-mode sidecar --output host-report`, then `fgview host-report`.
Single-file, sidecar, `--no-text-view` and patched uftrace JSONL kernel events
share this Timeline. Ordinary `trace-cmd report` text does not replace the binary
container; function_graph text carrying KVM comment events is also supported.

## A short event-only capture

On a **dedicated test Host**, record one second while running a brief VM workload:

```bash
instance="funcgraph-kvm-$(date +%s)"
sudo trace-cmd record -B "$instance" -C mono \
  -e kvm:kvm_entry -e kvm:kvm_exit \
  -e sched:sched_switch -e sched:sched_waking -e sched:sched_wakeup \
  -o host.dat -- sleep 1
funcgraph --no-config host.dat --raw-instance "$instance" \
  --timeline --filter --output host.html
```

This captures all matching Host events, not just one VM. On a busy Host, filter
KVM `common_pid` to target QEMU threads, scheduling by `prev_pid` / `next_pid`, and
wakeups by target `pid`. Filtering only the emitter misses other threads' wakes.
Use `-e kvm` instead of the boundary events for auxiliary evidence. Check event
availability and lost-event counts on the actual kernel.

Recording modifies tracefs. Use a fresh instance name: an existing one may be
reset. Follow your isolation and before/after verification procedure if other
tracing is active. Analysis only reads the file. This example intentionally does
not request `function_graph`; its availability in instances depends on the kernel.

### When you need actual kernel call stacks

Use `fgcapture` to record functions and events together. Omitting `-g / -n` avoids
function-name filtering:

```bash
sudo fgcapture --kernel --sched -p "$VCPU_TIDS" -e kvm -d 0.3 -o host-functions.txt
funcgraph --no-config host-functions.txt --timeline --enable-fold --filter --output host-functions.html
```

Use a dedicated idle Host; do not override an existing-session refusal.
`VCPU_TIDS` lists target Host vCPU threads, not an event PID filter. Start short and
check losses. This adds actual Host kernel rows and CALL STACK, not QEMU user or
Guest internal calls. Depth, arguments and return addresses depend on the running
kernel. See [extra events and Text visibility](capture.md#record-functions-and-extra-events-together).

## Read the report

- Headers show observed **vCPU IDs and Host TIDs**. Same-numbered vCPUs in different
  Host threads are never silently merged into one VM or track.
- The KVM row uses muted green for **Guest windows** (`kvm_entry → kvm_exit`) and
  blue-grey for **Host windows** (`kvm_exit → next kvm_entry`). Function rows and
  off-CPU overlays retain their original meanings.
- Hover for phase, wall duration and exit reason. Click for original events,
  payloads, CPU, flags, exact nanosecond timestamps and Text links.
- Details provide previous/next window navigation, Fit window, paged evidence and
  CSV export of **all events in the selected scope**, not just the visible page.
  JSON analysis export also retains events and explicitly typed KVM windows.
- Function search / call filters do not remove KVM evidence. Collapse tasks to
  reduce clutter. Non-KVM reports gain no extra controls.
- Text's **Events: shown / hidden** button hides event rows without removing any
  Timeline or exported evidence.

## Accuracy boundaries

Windows are **wall-clock boundary intervals, not functions or CPU time**. A Guest
window may include Host scheduling delays. A Host window can include KVM, QEMU,
preemption, sleep or waits; it does not prove a return to QEMU userspace. Guest RIP
payloads are not resolved through Host kernel source links.

If the raw decoder cannot format an auxiliary KVM event, its original fallback
text (including `[FAILED TO PARSE]`) remains visible as evidence; it is not
presented as successfully decoded fields. Entry/exit boundaries must have valid
event formats before they can be used to derive windows.

Only adjacent opposite boundaries in the same known Host thread are paired.
Repeated boundaries, changed vCPU IDs, backwards timestamps and unverified
cross-CPU migration clocks leave gaps. A loss marker conservatively invalidates
all pending pairs. Original events remain available. Missing initial entries are
not reconstructed. Open final windows have a dotted edge and **end not recorded**:
with a shared clock their observed extent stops at the last trace timestamp, not
at an invented completion. Without that clock they remain open boundary points.

Record with a shared Host clock such as `mono`. A local / unknown clock, including
text without clock metadata, does not prove cross-CPU alignment. Timestamp origins
are decimal strings to prevent JavaScript rounding large absolute times.

This does **not** record Guest internal calls, synchronize Host/Guest clocks or
infer cross-machine causality. Guest internals need a separate recording and
verified alignment. Host function stacks require actual Host `function_graph`
records. Enrich those with a matching **Host** `vmlinux` / source tree, never the
Guest kernel image.

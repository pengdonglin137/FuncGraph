# Unified recording with fgcapture

`fgcapture` keeps the kernel-only text recorder and adds user-only and mixed
recording through the same entry point. `fgcapture.sh --user` / `--mixed`
also delegate to it when the Python package is installed.

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
    --user-src samples --output mixed.html
```

User-only recording needs no root and never inspects tracefs. Mixed recording
is for an idle dedicated experimental host. Add matching `--vmlinux`,
`--kernel-src`, `--func-links --highlight-code` and a pinned source revision
when generating reports with kernel BTF/source. `--user-src` selects the
separate user source tree; it never receives kernel web links.

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

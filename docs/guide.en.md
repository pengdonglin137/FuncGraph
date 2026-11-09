# FuncGraph User Guide

This guide covers the whole workflow from capturing a trace to reading the report, and explains every field and color in each view. For the full option list run `python3 funcgraph.py --help`; for implementation details see [design.md](design.md). 中文版：[guide.cn.md](guide.cn.md).

## Contents

1. [Overview](#1-overview)
2. [Installation](#2-installation)
3. [Capturing a trace](#3-capturing-a-trace)
4. [Generating a report](#4-generating-a-report)
5. [Text view](#5-text-view)
6. [Timeline view](#6-timeline-view)
7. [Analysis panels](#7-analysis-panels)
8. [Compare view](#8-compare-view)
9. [Export and automation](#9-export-and-automation)
10. [FAQ](#10-faq)

---

## 1. Overview

FuncGraph reads the text output of the kernel `function_graph` tracer and produces a single HTML report that works offline. The report has these tabs:

| Tab | Content | Needs |
|---|---|---|
| **Text** | The trace line by line, with source locations, folding, filtering and a duration heatmap | always generated |
| **Timeline** | A flame chart over time, one track per task: x is time, y is call depth; plus analyses of errors, critical sections, scheduling latency and wakeups | `--timeline` |
| **Compare** | Per-function call counts and durations compared with another trace | `--compare BASE` |

A typical workflow:

```bash
# on the target (root): trace one command, with scheduler events and an overhead estimate
fgcapture.sh --sched --calibrate -o /tmp/ftrace.txt -- cat /nonexistent

# on the development machine: build the report
python3 funcgraph.py --timeline --enable-fold --filter --fast \
    --vmlinux vmlinux --kernel-src linux/ \
    --base-url https://elixir.bootlin.com/linux/v6.18/source \
    --output report.html ftrace.txt
```

## 2. Installation

Nothing to install: run `funcgraph.py` from the repository.

| Dependency | Used for | Required |
|---|---|---|
| Python 3.6+ | the program (standard library only; the BTF reader is built in) | yes |
| binutils `addr2line` (`aarch64-linux-gnu-addr2line` etc. when cross-compiling) | address to source resolution | with `--vmlinux` |
| pyelftools | address resolution in `--fast` mode (`fastfaddr2line.py`) | with `--fast` |
| Pygments | C syntax highlighting with `--highlight-code` | optional |
| git | reading a given revision with `--source-rev` | optional |

For cross-compiled kernels set the toolchain the same way as for the kernel's `scripts/faddr2line`:

```bash
export CROSS_COMPILE=aarch64-linux-gnu-     # GCC toolchain
export LLVM=1                               # or llvm-addr2line
```

## 3. Capturing a trace

### 3.1 With fgcapture.sh (recommended)

`fgcapture.sh` runs on the target as root. It sets up tracefs for the scenario, captures, checks for lost events and restores every setting it changed.

```bash
fgcapture.sh -d 0.5 -o /tmp/all.txt                  # whole system for 0.5 s
fgcapture.sh --sched -- cat /nonexistent             # one command (and its children), with scheduler events
fgcapture.sh --sched --calibrate -p 1234 -d 1        # one pid, with an overhead estimate
fgcapture.sh --args -g vfs_read,vfs_write -d 1       # with arguments, only these call graphs
fgcapture.sh -n __schedule -d 1                      # without the scheduler's internals
```

| Option | Meaning |
|---|---|
| `-o FILE` | output file, default `./ftrace.txt` |
| `-d SECONDS` | capture time when no command is given, default 1 s |
| `-p PID[,PID]` | trace only these pids (`set_ftrace_pid`) |
| `-g FUNC[,..]` | trace only the call graphs of these functions (`set_graph_function`) |
| `-n FUNC[,..]` | do not trace these call graphs (`set_graph_notrace`) |
| `-b KB` | per-CPU ring buffer size, default 16384 KB |
| `--sched` | also record `sched_switch`, `sched_waking`, `sched_wakeup` (for wakeup arrows and wait chains) |
| `--calibrate` | measure the tracing overhead with the same options first; the result goes into the first line of the output |
| `--args` | record function arguments (see 3.3) |
| `--keep` | do not restore the previous tracefs settings |
| `-- CMD ...` | run CMD and trace only it and its children: it starts stopped, its pid is written to `set_ftrace_pid`, then it continues |

Options enabled by default: `funcgraph-abstime`, `funcgraph-proc`, `funcgraph-retval`, `funcgraph-retaddr`, `funcgraph-tail`, `latency-format`, `funcgraph-duration`, `funcgraph-cpu`, `funcgraph-overhead` and `funcgraph-irqs`.

After capturing, the script checks for lost events. `WARNING: events were lost` means the ring buffer wrapped: increase `-b`, shorten `-d`, or narrow the capture with `-p`/`-g`.

> Settings are restored from a `trap` on exit; if the script is killed with `kill -9` that cannot run. Restore by hand with `echo nop > /sys/kernel/tracing/current_tracer`.

### 3.2 By hand

```bash
cd /sys/kernel/tracing
echo 0 > tracing_on
for o in funcgraph-abstime funcgraph-proc funcgraph-retval funcgraph-retaddr \
         funcgraph-tail latency-format; do echo 1 > options/$o; done
echo function_graph > current_tracer
echo 1 > tracing_on; sleep 1; echo 0 > tracing_on
cat trace > ~/ftrace.txt
echo nop > current_tracer
```

### 3.3 What each option contributes

FuncGraph accepts any combination of options; missing information is explained by notes at the top of the report.

| ftrace option | Provides | Without it |
|---|---|---|
| `funcgraph-abstime` | absolute timestamp of each line | no real time axis: calls are packed back-to-back by duration ("compressed time"); no gaps, off-CPU periods or wakeups |
| `funcgraph-duration` | duration of each function | durations are derived from the next timestamp and marked "estimated" |
| `funcgraph-proc` | task name and pid of each line | tasks are inferred from context-switch markers, otherwise one track per CPU |
| `latency-format` | flags column: irqs off, need-resched, hard/soft irq, preempt depth | context is inferred from interrupt entry function names; no irqs-off, scheduling-latency or critical-section analysis |
| `funcgraph-retval` | return values | no error paths |
| `funcgraph-retaddr` | call sites (return addresses) | no source locations, no call-site statistics, no inline frames |
| `funcgraph-tail` | the function name after every `}` | call pairing is slightly less robust (with retval the kernel prints the name anyway) |
| `funcgraph-args` | function arguments | arguments are just not shown |
| `sched_switch` / `sched_waking` events | task state at switch-out, who woke whom | the wait chain cannot name the waker |

**`funcgraph-args` behaves very differently between kernels:**

- **6.18 and other kernels without the BTF print optimizations**: formatting arguments while reading `trace` is very slow (about 36 ms per line on a Raspberry Pi 4), and with `funcgraph-retaddr` enabled no arguments are printed at all. Narrow the capture with `set_graph_function`, `set_ftrace_filter` or `set_ftrace_pid` first and disable retaddr.
- **Kernels with the BTF print optimizations** (for example the 7.2 trim_retval patches): arguments and return addresses can be printed together; 117k lines are read in about 1.1 s.

`fgcapture.sh --args` probes the kernel on a single function first and picks the right options automatically.

### 3.4 Tracing overhead

function_graph runs a hook at every function entry and exit, and that time is part of the measured durations. The more traced calls below a function, the more its duration is overstated. Measured values:

| Environment | Per traced child call | Per call (own measurement) |
|---|---|---|
| Raspberry Pi 4, 6.18 arm64, lockdep enabled | about 1.1 us | about 0.46 us |
| QEMU x86_64, 7.2 | about 0.2 us | about 0.1 us |

Capture with `--calibrate`, or pass `--overhead CHILD,SELF` when converting, and the report can show durations with the overhead subtracted (see 6.7).

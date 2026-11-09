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

## 4. Generating a report

### 4.1 Common combinations

```bash
# full report: text view (folding, filter, function links, highlighted source) + timeline
python3 funcgraph.py --timeline --enable-fold --filter --func-links --highlight-code --fast \
    --vmlinux vmlinux --kernel-src linux/ \
    --base-url https://elixir.bootlin.com/linux/v6.18/source \
    --output report.html ftrace.txt

# no vmlinux: the trace and the timeline, without source information
python3 funcgraph.py --timeline --enable-fold --filter --output report.html ftrace.txt

# very large trace (more than ~100k lines): timeline only
python3 funcgraph.py --timeline --no-text-view --vmlinux vmlinux --output report.html ftrace.txt

# narrow first: only the vfs_read call trees of pid 1234
python3 funcgraph.py --timeline --pid 1234 --graph vfs_read --vmlinux vmlinux --output report.html ftrace.txt
```

### 4.2 Options by topic

**Source locations**

| Option | Meaning |
|---|---|
| `--vmlinux PATH` | for address to source resolution, function links and BTF. Optional |
| `--kernel-src DIR` | kernel source root, used to show source and compute relative paths |
| `--fast` | resolve addresses with the bundled `fastfaddr2line.py` (needs pyelftools), much faster than the kernel script |
| `--base-url URL` | root of an online source browser such as bootlin; locations become links |
| `--func-links` | turn function names in the text view into source links too |
| `--highlight-code` | C syntax highlighting in source blocks (needs Pygments) |
| `--module-dirs DIR...` / `--module-srcs DIR...` | where module `.ko` files / module sources are |
| `--module-url URL:mod1,mod2` | online source URL for some modules; may be repeated |
| `--path-prefix PATH...` | build path prefixes to strip from DWARF file names |
| `--entry-offset N` | entry address offset for kernels built with `-fpatchable-function-entry` |

**Source origin** (default: the local files faddr2line finds)

| Option | Meaning |
|---|---|
| `--source-rev REV` | read files of this revision from the `--kernel-src` git repository (`git show REV:path`), useful when the work tree is on another branch |
| `--source-url TEMPLATE` | download files from a URL; `{path}` is the file path, `{rev}` the `--source-rev` value |
| `--source-cache DIR` | cache for downloaded files, default `~/.cache/funcgraph/src` |

```bash
--source-url 'https://raw.githubusercontent.com/torvalds/linux/{rev}/{path}' --source-rev v6.18
--source-url 'https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/plain/{path}?h={rev}' --source-rev v6.18.5
```

With either origin the tool checks whether the source matches the vmlinux and reports the result on the console and in the ⓘ info panel, e.g. `90% match the function names`. The check: in an inline chain, the line of an outer frame is where the inner function is called, so the inner function's name must appear on that line. Measured: 86–94% for the right version, 26–59% for a wrong one. Below 75% a warning says "probably a different version than the vmlinux": line numbers may not match.

**Report content**

| Option | Meaning |
|---|---|
| `--enable-fold` | folding of call blocks in the text view |
| `--filter` | the unified filter in the text view |
| `--timeline` | the Timeline tab |
| `--compare BASE` | the Compare tab against the trace BASE |
| `--no-text-view` | no per-line text view and no address resolution; only Timeline/Compare. For very large traces |
| `--btf FILE...` / `--no-btf` | BTF sources (e.g. `/sys/kernel/btf/vmlinux` copied from the target) / do not use BTF |
| `--overhead CHILD[,SELF]` | tracing overhead estimate in ns; default: read from the header written by `fgcapture.sh --calibrate` |
| `--export FILE.json\|FILE.csv` | also export the analyses (section 9) |
| `--output FILE` | output HTML, default `ftrace_viz.html` |

**Pre-filter**: applied before conversion; call trees are kept whole, never cut in the middle.

| Option | Meaning |
|---|---|
| `--pid 1,2` | keep only these tasks |
| `--comm RE[,RE]` | keep tasks whose name matches |
| `--cpu 0,1` | keep only these CPUs |
| `--time-range START:END` | keep calls that start in this window, with all their children. Absolute seconds (`2003249.69:2003249.70`) or offsets from the first timestamp with units (`+2ms:+5ms`); either end may be omitted |
| `--graph RE[,RE]` | keep only the call trees of these functions |
| `--notrace RE[,RE]` | drop the call trees of these functions |

### 4.3 Input

```bash
python3 funcgraph.py ftrace.txt                  # a file
python3 funcgraph.py ftrace.txt.gz               # .gz / .xz / .bz2 are decompressed
ssh target cat /sys/kernel/tracing/trace | python3 funcgraph.py -   # stdin
python3 funcgraph.py cpu0.txt cpu1.txt cpu2.txt  # several files merged by timestamp
```

Several files are merged by time when all have `funcgraph-abstime` timestamps; otherwise they are concatenated in the given order and the console says so.

### 4.4 Config file and presets

Options that never change go into an INI file, looked up as `--config FILE`, `./funcgraph.ini`, then `~/.config/funcgraph/config.ini`:

```ini
[default]
fast = true
filter = true
enable-fold = true

[preset pi4]
vmlinux = /vol_1t/raspi/linux/vmlinux
kernel-src = /vol_1t/raspi/linux
base-url = https://elixir.bootlin.com/linux/v6.18/source
cross-compile = aarch64-linux-gnu-
timeline = true

[preset vm72]
vmlinux = /vol_1t/Qemu/trim_retval/x86_64/vmlinux
kernel-src = /vol_1t/Qemu/trim_retval
source-rev = HEAD
timeline = true
```

```bash
python3 funcgraph.py --preset pi4 --output r.html ftrace.txt
```

Rules:
- `[default]` always applies, `--preset NAME` adds `[preset NAME]` on top, and the command line wins over both.
- Keys are the long option names without `--`. Switches take `true` or `false`; list options take space-separated values (`module-dirs = a b`).
- `cross-compile` and `llvm` set the matching environment variables unless they are already set.
- Unknown keys are an error. `--no-config` ignores all config files.

## 5. Text view

### 5.1 Layout

- **Header**: total lines, expandable lines, resolved kernel functions; `Filtered: N` while a filter is active. On the right: **Theme** (dark/light), **?** (shortcuts) and **ⓘ** (info panel: environment, processing times, source origin and version check, the options used).
- **Tabs**: Text / Timeline / Compare.
- **Top toolbar** (stays at the top while scrolling):

| Button | Action |
|---|---|
| Filter | show / hide the filter box |
| Top | back to the top |
| Expand All / Collapse All | open / close all source blocks |
| Copy | copy the visible lines as plain text |
| Heatmap | duration coloring on / off, legend on the right |
| Order: time / by task | line order: as captured (all CPUs interleaved by time) or grouped by task, see 5.6 |
| Source: chain / flat | layout of source blocks, see 5.5 |

### 5.2 Columns of a trace line

A line with all options enabled:

```
2003237.699097 |   0)  true-130265   |  d.h1. | + 12.500 us   |    link_path_walk(); /* <-path_lookupat+0x84/0x180 ret=-2 */
└─ timestamp ─┘   └CPU┘ └─ task ───┘     └flags┘ └─ duration ─┘   └ indent + function ┘ └─ call site ─────────┘ └return┘
```

| Column | Option | Meaning |
|---|---|---|
| timestamp | `funcgraph-abstime` | time of the event in seconds, microsecond resolution |
| CPU | `funcgraph-cpu` | CPU of the event |
| task | `funcgraph-proc` | `name-pid`; `<idle>-0` is the idle task |
| flags | `latency-format` | 5 characters, see below |
| duration | `funcgraph-duration` | duration of the function: on the call line for leaves, on the `}` line for others. The leading mark is the duration class, see below |
| function | | indented by call depth. `func() {` is an entry, `func();` a leaf (entry and return in one line), `}` a return |
| `/* <-caller+off/len */` | `funcgraph-retaddr` | call site: offset inside the calling function; `len` is the caller's size |
| `ret=` | `funcgraph-retval` | return value, see 5.3 |

**Flags column** (the latency-format definition of tracefs):

| Position | Char | Meaning |
|---|---|---|
| 1 interrupts | `d` | irqs disabled |
| | `D` | irqs and softirqs (BH) disabled |
| | `b` | only BH disabled |
| | `.` | neither |
| 2 need-resched | `N` | TIF_NEED_RESCHED set and the preempt count asks for a reschedule |
| | `n` | only TIF_NEED_RESCHED |
| | `p` | only the preempt count asks for it |
| | `L` / `l` | lazy need-resched (PREEMPT_LAZY); `L` also with the preempt request |
| | `B` / `b` | need-resched and lazy both set (`B` also with the preempt request) |
| | `.` | no reschedule pending |
| 3 context | `h` | hard interrupt |
| | `s` | softirq |
| | `H` | hard interrupt inside a softirq |
| | `z` / `Z` | NMI / NMI inside a hard interrupt |
| | `.` | task context |
| 4 preempt depth | `0`–`f` | low 4 bits of preempt_count, `.` for 0 |
| 5 migrate-disable depth | `0`–`f` | nesting of migrate_disable, `.` for 0 |

Note: the preempt depth is read inside the tracing hook, and the tracing path usually adds one. On 6.18 and 7.2 the normal value in task context is therefore `1`, not `.`. The timeline analyses detect this baseline automatically (7.3).

**Duration marks** (kernel `trace_find_mark()`):

| Mark | Duration | Heat level | Heatmap color |
|---|---|---|---|
| (space) | ≤ 10 us | 1 | very light green |
| `+` | > 10 us | 2 | light green |
| `!` | > 100 us | 3 | yellow |
| `#` | > 1 ms | 4 | amber |
| `*` | > 10 ms | 5 | orange |
| `@` | > 100 ms | 6 | red |
| `$` | > 1 s | 7 | dark red |

With the heatmap on, the whole line background has the color of its level.

### 5.3 Syntax colors

| Element | Color (light theme) |
|---|---|
| CPU number | bold red |
| timestamps and duration numbers | blue |
| unit `us` | bold purple |
| function name | bold purple; a link with `--func-links` |
| module function name | bold teal |
| hex values (arguments, addresses) | dark blue |
| comments `/* ... */` | grey italic |
| brackets, semicolons | bold dark grey |
| `ret=` with a known errno (e.g. -2) | **red on light red**, followed by the name: `ret=-2 /* ENOENT */` |
| other `ret=` values | bold grey |

The format of return values depends on the kernel:
- **6.18 and similar**: error codes are negative decimals (`ret=-2`), everything else is hex (`ret=0x0`). Void functions print `ret=` too, with whatever was left in the register; it means nothing.
- **Kernels with BTF-aware retval** (e.g. 7.2 trim_retval): void functions print no `ret=`; bool prints `ret=true/false`; signed integers are decimal, unsigned ones decimal or hex; types wider than long get `(trunc)`; pointers holding an ERR_PTR are printed as negative errnos.

### 5.4 Folding

With `--enable-fold` every `func() {` line has a ▶ icon:

- Click ▶ to fold/unfold the block; Shift+click folds/unfolds recursively.
- Keys `1`–`9`: show only calls up to depth N; `0`: unfold everything.
- A folded entry line shows a summary: number of child calls, total time, return value.
- Folding state is kept in the browser's localStorage, per report.

Background colors related to folding:

| Color | Meaning |
|---|---|
| very light green | entry line `func() {` of a block |
| very light blue | lines inside a block |
| very light red | exit line `}` of a block |
| stronger red | a folded entry line |
| green vertical line on the left | extent of the block, from entry to exit |

### 5.5 Source blocks

Lines with a call site can be expanded (click the line or press Enter). The block shows the source around the call site: 5 lines before and after, the current line highlighted and marked `>line<`.

When the call site is inside inlined code, faddr2line returns an inline chain. The **Source** button switches between two layouts:

- **chain** (default): outer to inner. `traced` is the function ftrace saw; below it each inlined `inline` function, indented one level, collapsible, with "→ inlines X here". Only the innermost one ("← the traced call happens here", the line that makes this call) is open. ↗ on each level is an online source link.
- **flat**: exactly like faddr2line's output, innermost first, everything open.

### 5.6 Grouping by task

ftrace prints the events of all CPUs ordered by time. With several busy tasks and frequent switches a task's calls are scattered: measured 25–58% of adjacent lines belong to different CPUs.

With **Order: by task**:
- Each task's lines are together, in their original order. Tasks with more lines come first, idle tasks last.
- Each group has a sticky header: `name pid · lines · CPUs it ran on`.
- After each context switch a grey italic summary line is inserted, e.g. `⇣ switched out on CPU 0 → idle-0; resumes at line 19703 after 7.640 ms (1 lines of other tasks in between)`: where it was switched out, who came next, when and at which line it resumes, and how many lines of other tasks lie in between. Separator and blank lines of the switch markers are hidden.
- Folding, filters, heatmap and source blocks keep working. **Order: time** restores the original order by line number.

### 5.7 Unified filter

Type `field:value` in the filter box, combine with `AND`, `OR`, `NOT` and parentheses, and press Enter or Apply. The field buttons below open drop-down lists of values.

| Field | Matches | Examples |
|---|---|---|
| `cpu` | CPU number (regex) | `cpu:0`, `cpu:0\|1` |
| `pid` | pid (regex) | `pid:1234` |
| `comm` | task name (regex) | `comm:bash` |
| `func` | function name of the line (regex), this line only | `func:mutex` |
| `graph` | the **whole call tree** of matching functions (like `set_graph_function`) | `graph:vfs_read` |
| `notrace` | hides the whole call tree of matching functions (like `set_graph_notrace`) | `notrace:__schedule` |
| `ret` | return value number (regex); `ret:all` matches every known error code | `ret:-22`, `ret:-2$`, `ret:all` |
| `params` | argument text (regex) | `params:folio` |
| `duration` | duration in us, with `>` `<` `>=` `<=` | `duration:>10` |
| `irqs` | flags position 1 | `irqs:d` |
| `resched` | flags position 2 | `resched:N` |
| `hirq` | flags position 3 | `hirq:h`, `hirq:s` |
| `preempt` | preempt depth, with comparisons | `preempt:>1` |
| `sort:desc` / `sort:asc` | sort the visible lines by duration | `duration:>0 sort:desc` |

Examples:

```
comm:cat AND graph:anon_pipe_read             the complete anon_pipe_read call trees of cat
graph:vfs_read AND NOT notrace:security_      vfs_read call trees without the security_* subtrees
ret:all AND comm:ls                           every line of ls returning an error code
irqs:d AND duration:>50                       calls longer than 50 us with irqs disabled
```

The filter is shared with the timeline (6.6).

### 5.8 Keyboard and mouse

| Action | Effect |
|---|---|
| `j` / `k` or ↓ / ↑ | next / previous expandable line |
| `h` / `l` | scroll left / right |
| Enter | open / close the source block of the line |
| Space / PageDown / PageUp | page |
| `gg` / `G` (or Home / End) | top / bottom |
| `1`–`9` / `0` | fold to depth N / unfold all |
| Esc | clear the selection |
| `?` | shortcut help |
| click a line number | copy an anchor link (`#Lnumber`) to share the exact line |
| Alt + click a line | show this call in the timeline |

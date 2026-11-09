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
| Python 3.8+ | the program (standard library only; the BTF reader is built in) | yes |
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

The shortest way is `fgreport` (README, "Quick start"): store the kernel paths once with `fgreport init`, then `fgreport trace.txt` writes `trace.html` with all features on. Its presets live in the config file of 4.4 and work with `funcgraph.py --preset` as well. Calling `funcgraph.py` directly:

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

![text_view](img/text_view.png)

*Text view (sample.html, filter `graph:udp_sendmsg`, first line expanded). 1 header, 2 tabs, 3 toolbar, 4 filter (5.7), 5 one trace line (5.2), 6 the expanded source block as an inline chain (5.5).*

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

## 6. Timeline view

### 6.1 Layout

![tl_layout](img/tl_layout.png)

*The timeline (sample.html, `__x64_sys_sendto` selected); the numbers match the list below.*

From top to bottom:

1. **Toolbar, first row**: Color (color mode), Overlay (layer switches), function search, error navigation.
2. **Toolbar, second row**: Fit (show all), Tracks (expand / collapse all tracks), Overhead (measured / subtracted), Inline (inline frames), Errors, Sections, Sched latency (the analysis panels), Notes (collapse / expand the notes), Max (hide the page header and tabs, `M`), ? (help and legend), then **Filter**: the filter expression shared with the text view, see 6.6. Narrow windows wrap the toolbar into more rows.
3. **Notes**: what this trace lacks and what that means ("No timestamps…", "Arguments are not printed…"). Blue notes are information, yellow ones need attention.
4. **Overview strip**: the whole trace in miniature. Blue bars show how busy each moment is, red lines are error origins (tall: reached user space, short orange: handled by a caller), the blue frame is the visible range. Click or drag to jump, use the wheel to zoom.
5. **Time axis**: the right side shows the visible span (`span`) and the absolute start time (`start`), plus `selected` for a range selection; the time under the cursor is shown next to it.
6. **CPU tracks** (only with timestamps) and **task tracks**. Time counts from the earliest event in the trace (possibly a scheduler event before the first call), so the first call need not be at 0; the initial view and Fit show the first call to the end, pan left to see earlier events.
7. **Bottom panel**: opens when a slice is clicked or an analysis panel is opened; drag the bar above it to resize (6.5).

The **time mode** depends on the columns in the trace and is named at the right of the axis:

| Mode | Condition | x axis |
|---|---|---|
| abs | `funcgraph-abstime` | real time, gaps are real |
| rel | relative timestamps | same |
| compressed | durations only | calls of a task are packed back-to-back; no gaps ("compressed time") |
| count | neither | each leaf counts as one unit: call order only |

### 6.2 Tracks

**Task tracks**: one per task, busiest first; idle tasks last and collapsed.

- Header: `name pid`, then the CPUs it ran on, call count and error count. While filtering also the number of matches (`N matched`); if the task was renamed, `was: old name` (e.g. bash before exec). Without `funcgraph-proc` the header reads `CPU N (no funcgraph-proc)`.
- Click the header to collapse / expand the track. A collapsed track is one thin row with the outermost calls.
- Inside a track x is time and y is call depth, outermost at the top. **A slice's width is the function's measured duration.**

**CPU tracks** (`CPUs` at the top): one row per CPU showing which task ran on it.

- Each segment is colored by task name (same task, same color) and labeled `name pid` when wide enough.
- The thin strip at the bottom of a segment is interrupt time: **orange** hardirq, **green** softirq, **purple** NMI.
- Hover: how long the task ran on this CPU in one go and how much of it went to hard/soft interrupts; click to jump to the task's track.

### 6.3 Color modes (Color)

| Mode | The color shows |
|---|---|
| **Context** (default) | execution context: **blue** task, **green** softirq, **orange** hardirq, **purple** NMI. Functions of the same context differ slightly in hue and lightness so neighbors can be told apart |
| **Function** | the function name: same function, same color; find the calls of one function everywhere |
| **Self time** | self time (without children), log scale, from **blue** (shortest) to **red** (longest): where the time is really spent |
| **CPU** | the CPU a call ran on, one color per CPU, grey if unknown: shows migrations |
| **Module** | the kernel module, one color per module, light grey for the core kernel. Hidden when the trace has no module functions |

Without `latency-format` the context in Context mode is inferred from interrupt entry functions (`gic_handle_irq`, `__sysvec_*`, `handle_softirqs`, …); such slices say `(inferred)` in the tooltip.

### 6.4 Overlays and markers

Each overlay can be switched off; buttons of overlays without data are hidden.

| Overlay | Looks like | Meaning |
|---|---|---|
| **Errors** | **solid dark red** slice | error origin, and the error reached user space (a `*_sys_*` in the chain) |
| | **solid orange** slice | error origin, handled by some caller |
| | **light red with red outline** | error propagation: returns the same errno as a child |
| | **light orange with orange outline** | propagation of a handled error |
| | small **triangle** (red / orange) | an error origin too narrow for a label |
| | label `func = ENOENT` | error origins get the errno name after the function name |
| **IRQs off** | **hatching** on a slice | irqs were disabled when the call started |
| | **thin dark grey line** under the track header | periods with irqs disabled (real start and end) |
| **Resched delay** | **amber triangle** in the top right corner of a slice | need-resched was already set when the call started: a reschedule was due but had not happened |
| | **amber band** under the track header | need-resched window: from setting the flag until the schedule happens, i.e. scheduling latency |
| **Off-CPU** | grey hatched segment labeled `off-CPU 7.640 ms · do_wait [S]` | the task was not on a CPU; drawn one level below the frame that was open. Label: duration, function it waited in, state at switch-out (`S` interruptible sleep, `D` uninterruptible, `R` still runnable) |
| | the same with an **amber tint** | preempted (it could have kept running) |
| **Wakeups** | **teal arrow** from a dot at the waker to where the woken task resumes | who woke whom and when (needs `sched_waking` or `sched_wakeup`) |
| | only a small head | bunched wakeups show just the end point. Hovering or selecting a task, or hovering an off-CPU segment, draws its full arrows; the top right says "N of M wakeups bunched" |
| **Preempt depth** (off by default) | grey bar at the bottom of a slice, darker for deeper | |
| **Markers** | **dotted right edge** | entry seen, return not (the capture ended, or events were lost) |
| | **dotted left edge** | return seen, entry not (the call started before the capture) |
| | **small purple square** top left | the call site's function is not the parent slice: an untraced frame (notrace, assembly entry, tail call) or inlined code in between |
| | **red underline** | a sleeping function called in atomic context (7.4) |

![tl_errors](img/tl_errors.png)

*An error path (the second `recvfrom`): `udp_recvmsg` is the origin (solid red, label `= EAGAIN`), the frames above are propagation frames (light red, red outline); the tooltip gives the errno and its fate.*

![tl_irqoff](img/tl_irqoff.png)

*Irqs off: `do_softirq` and `__do_softirq` are hatched (irqs off when they started); the dark grey line under the track header shows the irqs-off periods; calls in the softirq are green.*

![tl_offcpu](img/tl_offcpu.png)

*Off-CPU and wakeups (tests/data/pi4_sched.txt): light grey hatched segments are time off the CPU, teal arrows go from the waker to the woken task; hovering an off-CPU segment shows its state and waker.*

![tl_untraced](img/tl_untraced.png)

*Untraced frames (tests/data/pi4_nodur.txt): two levels are missing between `_raw_spin_unlock` and the `unwind_find_stack` calls inside, drawn as a dashed `untraced frame`; this fixture has no call sites, so the name is unknown.*

Two things that are easy to misread:

- **Hatching shows the state when the call started.** The latency column records the irq state of each printed line, and a slice uses its entry line. Disabling or enabling irqs in the middle of a function (e.g. the calls after `spin_lock_irqsave`) does not show on the slice itself. For how long irqs were really off, look at the dark grey line under the track header or the Sections panel (7.3), which lists each region's start, end and what ran inside.
- **Two kinds of hatching**: the darker hatching on a slice is irqs off and covers only that slice; the very light grey hatched segment with an `off-CPU …` label, reaching from one level down to the bottom of the track, is time the task was not on a CPU and has nothing to do with interrupts.

Other visuals:

- **Dimmed**: slices excluded by search or filter are drawn at 18% opacity.
- **Dense blocks**: neighboring slices narrower than 1.5 px are merged into 1 px blocks colored like their first slice; zooming in brings them back individually.
- **Selected**: thick dark outline. **Hovered**: thin outline. As in Perfetto, the selected slice's start and end are marked with two blue lines over all tracks (light blue in between); the time ruler shows the same span as a blue bracket with its duration, and the overview strip shows where it is. These marks are hidden while a range is selected.
- **Inline frames** (Inline view): translucent grey with a dashed outline, labeled `inline name`. See 6.8.
- **Untraced frames** (always shown): translucent grey with a dashed outline, labeled `untraced name`. The kernel's call depth sometimes jumps by two or more levels, e.g. on arm64 `unwind_find_stack` appears two levels below `__might_sleep` (no entry was recorded for `arch_stack_walk` in between), and so do the interrupt entry calls `irq_enter_rcu` and `do_interrupt_handler` (the untraceable `el1_interrupt`). That used to leave an empty row. These calls now belong to the enclosing slice (its self time no longer includes them) and the missing level is drawn as an untraced frame named after the calls' return address (their caller); further missing levels are shown as `untraced frame` (name unknown). Its extent is the first to the last call inside; the real start and end are unknown. Tooltip and detail panel say so, and call stacks list these frames (marked `untraced`).

### 6.5 Tooltip and detail panel

![tl_detail](img/tl_detail.png)

*Detail panel of `udp_sendmsg` (dragged taller): values, the call site `net/socket.c:800` and its source block on the left; the time breakdown, call stack (inline frames in italics) and children on the right.*

**Hovering a slice** shows:

| Field | Meaning |
|---|---|
| name `[module]` | module functions get the module name |
| duration, `self` | measured and self time; `(estimated)` when there was no duration column and the value comes from timestamps |
| start | time since the start of the trace, absolute timestamp in parentheses |
| task | task and CPU |
| context | context plus the decoded flags: irqs off, BH off, need-resched kind, preempt N, migrate-off N; `(inferred)` if inferred |
| return | return value; for errors the errno name, the role (error origin / propagated) and the fate (returned to user space / handled by caller / propagates beyond trace). `unverified: no BTF type` means it was judged by value only |
| caller | call site; `(untraced frame between)` as for the purple marker |
| source | source position `file:line` of the call site, i.e. the line that made this call (needs the text view and `--vmlinux`) |
| inlined in | inline chain around the call site |
| args | arguments (only with `funcgraph-args`) |

**Hovering an off-CPU segment**: wait time, function it waited in, state at switch-out, CPU it left and CPU it resumed on, waker.

**Clicking a slice** opens the detail panel below:

- Tags next to the title: context (`?` if inferred), irqs off, BH off, need-resched, preempt off (preempt count above the trace's baseline, `×N` for N extra levels; hover the tag for the numbers. function_graph disables preemption itself while recording, so on `CONFIG_PREEMPTION` kernels every line's preempt digit is one too high: the baseline is the lowest value seen in task context, capped at 1, so a trace that lies entirely in a preempt-off region is not hidden. Preempt off, Sections, atomic sleeps, the Preempt depth overlay and Sched latency's max preempt off all subtract it; the text view shows the raw digit, so filter with e.g. `preempt:>1` there), migrate off, error origin/propagated, may sleep in atomic context, no exit, no entry, estimated.
- Left column: duration, self (and its share), start, task, depth, flags, return, call site, args, trace lines (entry and exit line numbers; click to open the text view).
  - Below **call site**: the source position of the call (`file:line`, click to open the online source); `in FUNC (inlined)` when the call sits in inlined code.
  - **Call site source**: the call site's source block, the same as when expanding the line in the text view (highlighting, `>line<` marks the call, inline chain in the chain/flat layout of the Source button). Click the title to collapse it; the choice is remembered. It reuses the block the text view already resolved, so the report gets no extra data. Without `--kernel-src` / `--source-rev` / `--source-url` only the position link is shown, with `--no-text-view` neither.
- Right column:
  - **Where the time went**: a stacked bar splitting the measured duration (the parts add up to it exactly):

    | Color | Part |
    |---|---|
    | dark blue | own code: the function's own code |
    | light blue | traced callees |
    | orange | interrupts / softirq that interrupted the call |
    | grey | blocked: sleeping, waiting |
    | yellow | preempted |

    Below it: how many traced calls are inside, and roughly how much is tracing overhead (with an estimate).
  - **Error chain**: the error's path from origin to outermost frame.
  - **Call stack**: the stack with each frame's duration; inline frames in italics, marked `inline`. If the stack starts before the capture, "… N frame(s) outside trace window".
  - **Children**: the children with the most time, `×N` = number of calls.
- Links at the top right: **Duration distribution (H)** (7.2) and **Open in text view ↵**.

The detail panel and the analysis panels share the bottom area. Drag the bar above the panel to change its height; the canvas grows or shrinks accordingly. Double-click the bar to restore the default (32% of the window height, between 180 and 400 px). The height is stored in the browser as a fraction of the window, so other reports and other window sizes keep the same split.

For a larger canvas: **Max** in the toolbar (`M`) hides the page header and the view tabs, press it again to restore them. **Notes N** collapses or expands the notes about this trace below the toolbar (missing options, overhead estimate, ...); the choice is remembered.

### 6.6 Search and filter

- **Search box** (`/`): a function-name regex. Non-matching slices are dimmed and the number of matching calls is shown; Enter or `N` goes to the next match, Shift+Enter or Shift+`N` to the previous one.
- **Filter bar**: the same syntax as the text view's unified filter (5.7), kept in sync with it. Non-matching calls are dimmed; error navigation, range summaries and all analysis panels only count matches; the right side shows `N of M calls`. **Hide unmatched** hides tracks without matches, **Clear** removes the filter.

### 6.7 Measured durations and overhead subtraction

With an overhead estimate (the header written by `fgcapture.sh --calibrate`, or `--overhead`) the **Overhead** button (`O`) appears:

- **measured**: measured durations.
- **subtracted**: tooltips, the detail panel, summaries and analysis panels use durations with the overhead removed: `measured - per-child overhead × traced descendants - per-call overhead`, never below 0.

**Slices are always placed and sized by measured time**; only the numbers change. The correction is an estimate: overhead depends on CPU, frequency and kernel configuration, and is off on paths with many interrupts or different cache behavior.

### 6.8 Inline frames (Inline)

![tl_inline](img/tl_inline.png)

*Inline view: dashed frames are inlined functions (`inline __do_sys_sendto`, ...); the tooltip explains where their extent comes from.*

With `--vmlinux` and `funcgraph-retaddr` the tool knows whether a call site is inside inlined code. **Inline** (`I`) switches between two views and is remembered:

- **hidden**: only the frames ftrace traced.
- **shown**: inline frames between a parent and its children. Consecutive calls under the same parent that sit in the same inlined code are merged into one inline frame.

An inline frame **starts and ends** with the first and the last traced call inside it. Inlined code has no entry or return events, so the real start may be earlier and the real end later; tooltip and details say so.

Tooltip and detail panel of an inline frame:

| Field | Meaning |
|---|---|
| traced extent | span from the first to the last traced call |
| start / end | start and end time |
| inlined into | function, file and line it was inlined into |
| defined in | file that defines the inlined function |
| traced calls | traced calls inside and their total time |
| between them | span minus those calls: the inlined code itself, untraced calls, interrupts |
| Inline chain at the call site | the full chain, this level in bold |
| Traced calls inside | each call inside; click to go there |

In both views the call stacks list the inline frames.

### 6.9 Keyboard and mouse

| Action | Effect |
|---|---|
| `W` / `S`, or Ctrl + wheel | zoom in / out around the mouse |
| `A` / `D`, ← / →, drag, Shift + wheel | pan |
| wheel | scroll the tracks |
| `0` or Home | show all |
| `F` | zoom to the selected slice or range |
| double-click a slice / off-CPU segment | zoom to it |
| click a slice | select it and open the detail panel |
| click an off-CPU segment | open the wait chain (7.5) |
| click a track header | collapse / expand the track |
| Shift + drag | select a time range in this track and summarize it (7.1) |
| Shift + Alt + drag | the same over all tracks |
| ↑ / ↓ | select the parent / first child |
| `[` / `]` | previous / next sibling |
| `E` / Shift + `E` | next / previous error origin |
| `/`, `N` / Shift + `N` | search, next / previous match |
| `H` | duration distribution of the selected function |
| `O` | measured / overhead subtracted |
| `I` | hide / show inline frames |
| `M` | maximize (hide page header and tabs) / restore |
| drag / double-click the bar above the panel | resize the bottom panel / reset it |
| Enter | open the selected call in the text view |
| Esc | close the help; clear selection and range |
| `?` | help and legend |

## 7. Analysis panels

Analysis panels open in the detail area below the timeline and follow the filter and the Overhead switch. Column headers sort (the sorted column is underlined); rows jump to the timeline.

### 7.1 Range summary (Shift + drag)

![an_range](img/an_range.png)

*Range summary after selecting the time of `__x64_sys_write`: functions by self time.*

Shift + drag on a track selects a time range in that track; Shift + Alt + drag selects over all tracks. The summary shows:

- Tags: range length, track, number of calls, self time per context (task / softirq / hardirq / NMI) and off-CPU time.
- Table per function: calls (calls starting in the range), self, inclusive, max (longest call), errors (error origins). Calls partly inside the range count proportionally. Clicking a row puts the function into the search box and highlights it.

### 7.2 Duration distribution (H)

![an_dist](img/an_dist.png)

*Duration distribution (`unwind_find_stack.constprop.0` in tests/data/pi4_sched.txt, 115 calls): percentiles, log histogram, slowest calls.*

Press `H` with a slice selected, or click **Duration distribution** in the detail panel:

- Left: min, p50, p90, p99, max and mean over all calls of the function.
- **Histogram**: x is duration (log scale), y the number of calls (log scale). The blue line is p50, the amber one p99; bars containing error origins are red. Click a bar to list its calls below.
- **Slowest calls**: duration, self, task, start, call site, and `×p50` (how many times the median). Click to go to the call.
- **By call site**: one row per call site with calls and p50/p99/max; the same function called from different places can behave very differently. Click a row to restrict the distribution to that call site, click again to undo.
- **Slow vs fast** button: compares the slowest 10% with the fastest half of the calls: per call, the average number of calls and self time of every function in their subtrees, plus `(own code)`, `(interrupts / softirq)`, `(blocked off-CPU)` and `(preempted)`, sorted by the difference. The top rows say what the slow calls do in addition. Example: slow `kmem_cache_alloc` calls all add `___slab_alloc`, the allocator's slow path.

### 7.3 Critical sections (Sections)

![an_sections](img/an_sections.png)

*Sections panel (sample.html): irqs-off / preempt-off / BH-off regions, one row each, with where they started and ended and the functions with most self time inside.*

Every region in task context with **irqs off**, **preemption off** or **BH off**; the buttons at the top switch between the three kinds and show their counts:

| Column | Meaning |
|---|---|
| duration | length; red above 100 us |
| start / task | start time and task |
| started in / ended in | the traced function on top of the stack when the flag changed. `top level`: outside any function; `switched out / end`: the task was switched out or the capture ended; `interrupt`: an interrupt came in (so irqs had been re-enabled in untraced code) |
| max depth | maximum preempt depth (preempt off only) |
| top self time inside | the 3 functions with the most self time inside: what ran while the section was held |

The preempt depth is relative to a baseline: the lowest preempt depth seen in task context (1 on both 6.18 and 7.2), shown in the panel text.

Precision: enabling/disabling irqs or preemption is usually inlined code that does not appear in the trace, so a region's ends are only known up to the two traced calls around the flag change. Clicking a row selects the region in the timeline.

### 7.4 Error summary (Errors)

![an_errors](img/an_errors.png)

*Errors panel (tests/data/pi4_sched.txt): grouped by syscall, origin and errno; `?` means not verified (no BTF).*

- At the top (if any): **Sleeping functions called in atomic context**. Calls of functions that may sleep (`__might_sleep`, `mutex_lock*`, `down*`, `wait_for_completion*`, `msleep`, `schedule_timeout*`, …) with irqs off, preemption disabled, BH disabled or from an interrupt; with the reasons, the parent function and the count. Click to go to the call.
- The table groups error chains by **syscall / entry → origin → errno**:

| Column | Meaning |
|---|---|
| syscall / entry | the system call (without prefixes such as `__x64_sys_`); if there is none, the outermost function, tagged `no syscall` |
| origin | the error origin: the first function returning this errno while none of its children returned the same |
| errno | number and name |
| count | occurrences |
| to user | of these, how many reached user space (red) |
| handled | how many were handled by a caller |
| tasks | number of tasks involved |
| chain | longest propagation chain in frames; `?` when some frames are not type-verified |

Click a row to go to the first occurrence, click again for the next one (`occurrence k / n`).

**Minor errors** are hidden by default: single-frame origins handled at once by the caller, from functions whose negative returns never propagate anywhere in the trace. These are usually signed values that are not errnos (e.g. `memcg_state_val_in_pages` returns page deltas like -1) or normal probing (`load_script` returning -ENOEXEC while the binfmt handlers are tried). They are not marked on slices, not counted in navigation, the overview or Compare; the checkbox *show N minor* in the Errors panel brings them back, and `--export` marks them with `"minor": true`.

**How errors are decided**:

1. With BTF (the `.BTF` section of `--vmlinux`, files given with `--btf`, or a kernel that already formats return values by type), by the function's return type:
   - void, unsigned, bool, enum: never an error;
   - signed integer: truncated to the type width, an error if in [-4095, -1];
   - pointer: `IS_ERR` semantics; NULL is not an error but is marked;
   - comparators (names with `cmp`/`compare`) are not errors: they return differences.
2. Without BTF only the value can be used, and leftover register values of void functions cause false positives; such results are marked unverified.

The error navigator in the toolbar (`◀ N errors · to user ▶`): click the label to switch between "errors that reach user space" and "all errors".

### 7.5 Wait chain (click an off-CPU segment)

![an_wait](img/an_wait.png)

*Wait chain (tests/data/pi4_sched.txt): `python3` slept 1.934 ms, was woken by a hardirq on CPU 2 and ran again 526 us later.*

One row per wait, starting with the clicked off-CPU segment:

| Column | Meaning |
|---|---|
| task / waited | task and wait time |
| waiting in | function it waited in (first frame on the stack that is not scheduler code), state at switch-out in brackets |
| woken by | the waker, with a context tag if the wakeup came from an interrupt |
| waker was in | what the waker was doing: the first frame above `try_to_wake_up`, `__wake_up*` and similar, e.g. `anon_pipe_write` |
| wake latency | time from the wakeup until the task ran again; red above 1 ms |
| run again on | CPU it resumed on |

If the waker itself had been waiting before, the next row shows what it waited for, and so on. The chain ends at wakeups from idle or interrupt context.

The waker is taken from `sched_waking` when available: it fires in the waker's context. `sched_wakeup` can fire in an IPI on the target CPU, and the waker would then look like idle. Enable `sched_waking` when capturing; `fgcapture.sh --sched` does.

### 7.6 Scheduling latency (Sched latency)

![an_lat](img/an_lat.png)

*Sched latency panel (tests/data/pi4_sched.txt): each need-resched window with its length, the deepest preempt off inside, irqs-off time and the function with most self time.*

Each **need-resched window**, from setting the need-resched flag until the task is switched out or the flag is cleared:

- Tags: number of windows, total, longest. Below them, the functions with the most self time over all windows: who delays the reschedule.
- Columns: latency (red above 100 us), start, task, max preempt (maximum preempt depth in the window), irqs-off (time with irqs disabled in the window), top self time inside, ended by (flag cleared / switched out / trace end).
- Clicking a row selects the window in the timeline.

Needs `latency-format` and timestamps.

## 8. Compare view

![compare](img/compare.png)

*Comparing samples/sample_io_slow.txt with samples/sample_io.txt: in the slow run the softirq of `sendto` freed 55 more skbs (`napi_consume_skb` etc. are new), traced time +134%.*

```bash
python3 funcgraph.py --compare before.txt --output cmp.html after.txt
```

The current trace (`after.txt`) and the base (`before.txt`) are rebuilt the same way and summarized per function; pre-filters apply to both. `--timeline` is not needed: sorting controls and browser CSV export work independently.

- **Tags at the top**: changes of traced time (total of outermost calls), calls and error chains. Changes under 5% are grey, increases red, decreases green. A yellow warning appears when the two traces have different time modes (e.g. one without timestamps).
- **Table**: for every metric three columns: base, current, Δ.

| Metric | Meaning |
|---|---|
| calls | number of calls |
| self time | self time (inclusive minus the traced direct children) |
| inclusive | total time |
| mean incl | average total time per call |
| max | longest call |
| errors | error origins |

- **Δ colors**: **red** increase, **green** decrease, **grey** under 5%. The bar next to the sorted metric shows the size of the change. **new** after a name: only in the current trace; **gone**: only in the base.
- **Controls**: function regex filter; **changed only** retains changes in any of calls, inclusive time, self time, maximum duration, or errors; sort by `|Δ|`, `Δ`, `Δ%`, `current`, `base`; click a header to choose the metric. With overhead estimates for both traces, **subtract tracing overhead** appears. An empty result displays an explanation.
- **Narrow windows and scrolling**: table height adapts to the viewport and controls above it. Function names stay pinned when scrolling horizontally, and headers stay pinned vertically. Use Tab to focus the table region and arrow keys to scroll; hover over a truncated function name to see it in full.
- **Export CSV**: downloads every matching function in the current regex, changed-only, sort, and overhead-adjustment state, not just the loaded rows. No report regeneration is needed. Pending filter input is applied before export; invalid regexes show an explanation and disable export, as do empty results. Press Enter to apply a filter immediately.
- **CSV format**: `function` is followed by `base_`, `current_`, and `delta_` columns for each metric; deltas are current minus base. Duration column names end in `_ns` (nanoseconds); calls and errors are counts. Files use UTF-8 with a BOM and escape commas, quotes, and line breaks. Formula-like function names are prefixed with an apostrophe for spreadsheet safety. The filename distinguishes `raw` from `adjusted` durations; summary pills always retain measured values.
- **Large tables**: initially loads up to 400 rows; the bottom prompt appends up to 1000 at a time without replacing existing rows or sorting again. Filtering, sorting, and overhead changes recompute the result. Delta bars share a scale across all matching results, so loading more rows does not change earlier bars.

The traces should have similar tracing overhead (same options, same machine) for the differences to mean something.

## 9. Export and automation

`--export FILE.json` or `--export FILE.csv` writes the analyses next to the HTML; `--timeline` is not needed:

```bash
python3 funcgraph.py --export result.json --output report.html ftrace.txt
```

Durations in the JSON are in ns, times relative to the first event:

| Key | Content |
|---|---|
| `meta` | trace name, time mode, capabilities (caps), overhead estimate, number of calls |
| `functions` | per function `name, calls, total, self, max, p50, p90, p99, errors`, sorted by self |
| `errors` | per error chain `origin, top, entry, errno, name, fate` (user / handled / escaped) `, verified, frames, task, pid, start` |
| `sections` | critical sections `kind` (irqs_off / preempt_off / bh_off) `, duration, task, pid, start, started_in, ended_in, max_depth` |
| `sched_latency` | need-resched windows `task, pid, start, duration, max_preempt, irqs_off` |
| `atomic_sleep` | sleeping functions in atomic context `function, reasons, parent, task, pid, start` |
| `off_cpu` | off-CPU segments `task, pid, start, duration, waiting_in, state, preempted, woken_by` |

The CSV contains only the `functions` table.

A CI check:

```bash
python3 funcgraph.py --export r.json --no-text-view --timeline --output r.html ftrace.txt
python3 - <<'EOF'
import json, sys
d = json.load(open('r.json'))
f = {x['name']: x for x in d['functions']}
if f.get('vfs_read', {}).get('p99', 0) > 50_000:            # p99 above 50 us
    sys.exit('vfs_read p99 regression')
if any(s['kind'] == 'irqs_off' and s['duration'] > 200_000 for s in d['sections']):
    sys.exit('irqs off for more than 200 us')
if d['atomic_sleep']:
    sys.exit('sleeping function called in atomic context')
EOF
```

## 10. FAQ

**The report is too large; the browser is slow or does not open it**

The text view creates DOM for every line; above ~150k lines the console warns. Either narrow the trace with a pre-filter (`--pid`, `--comm`, `--graph`, `--time-range`) or build only the timeline with `--timeline --no-text-view` (measured: 362k lines → 15.7 MB, opens in about a second).

**The timeline has no gaps and says compressed time**

The trace was captured without `funcgraph-abstime`.

**No interrupts, irqs-off or scheduling latency**

`latency-format` was off. Without the flags column the context is inferred from interrupt entry functions and marked `(inferred)`.

**Lots of bogus errors such as `do_raw_spin_unlock = -1`**

Leftover register values of void functions. Give a vmlinux with a `.BTF` section (`CONFIG_DEBUG_INFO_BTF=y`) or `--btf` with `/sys/kernel/btf/vmlinux` copied from the target, and return types filter them out. Kernels with BTF-aware retval do not print `ret=` for void functions at all.

**`funcgraph-args` is on but no arguments appear**

On 6.18 and similar kernels arguments are not printed while `funcgraph-retaddr` is on. Capture with `fgcapture.sh --args`, which handles this.

**Source lines do not match**

The source is a different version than the vmlinux. Check the Source line in the ⓘ info panel, or use `--source-rev` with the revision the vmlinux was built from.

**Some slices are blank in the overview and appear only when zooming in**

A drawing bug of older versions, fixed. Regenerate the report.

**The waker in a wait chain is `<idle>`**

The wakeup came from an interrupt, or the trace has `sched_wakeup` but no `sched_waking`. Capture with `--sched`, which enables `sched_waking` too.

**One trace file per CPU**

Pass them all to funcgraph.py; they are merged by timestamp.

**Source code on a machine without a kernel tree**

Use `--source-url` with `--source-rev`. Building the report needs network access (downloads are cached); the report itself is a single offline file.

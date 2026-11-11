#!/usr/bin/env python3
"""Timeline model builder for FuncGraph.

Rebuilds per-task call slices from function_graph text output and produces a
compact, column-oriented data set for the Timeline (flame-chart over time) view.

The line grammar follows kernel/trace/trace_functions_graph.c. Every prologue
column is optional, so traces captured with any combination of these options
are accepted:

    funcgraph-abstime  "%5lu.%06lu |  "       absolute timestamp (us precision)
    (rel time)         "%9llu us |  "         relative timestamp
    funcgraph-cpu      " %*d) "
    funcgraph-proc     "comm-pid | "
    latency-format     " dNh2. | "            irqs-off/need-resched/hardirq-softirq/preempt[/migrate]
    funcgraph-duration "+ 12.345 us   |  "    or "              |  " for nested entries
    then depth*2 spaces of indentation and the function body:
        func(args) {  [/* <-caller+off/len [mod] */]
        func(args);   [/* <-caller+off/len [mod] ret=X */]
        }             [/* func [mod] ret=X */]
        /* event or trace_printk text */
        ==========> | / <========== |                (funcgraph-irqs)
    plus context-switch markers " 3)  sh-1 => <idle>-0 " and "CPU:N [LOST M EVENTS]".

Timing modes, picked from what the trace contains:
    abs         absolute timestamps (+ durations when present)
    rel         relative timestamps
    compressed  durations only: slices are packed back-to-back per task
    count       neither: each leaf is one unit (call-order view)
"""

import re
import time

# ------------------------------------------------------------------
# Line grammar
# ------------------------------------------------------------------
RE_ABS = re.compile(r' *(\d+)\.(\d{6,9}) \|  ')
RE_REL = re.compile(r' *(\d+) us \|  ')
RE_CPU = re.compile(r' *(\d+)\) ')
RE_PROC = re.compile(r' *(.+?)-(\d+) *\| ')
RE_LAT = re.compile(r' ([.dDb][.BNLbnpl][.ZzHhs][.0-9a-f][.0-9a-f]?) \| ')
RE_DUR = re.compile(r'([ +!#*@$]) (\d+(?:\.\d+)?) us *\|  ')
RE_DUR_NOMARK = re.compile(r' {1,2}(\d+(?:\.\d+)?) us *\|  ')
RE_FILL = re.compile(r' {14}\|  ')

RE_SWITCH = re.compile(r'^\s*(?:(\d+)\)\s+)?(.+?)-(\d+)\s+=>\s+(.+?)-(\d+)\s*$')
RE_LOST = re.compile(r'^CPU:(\d+) \[LOST (\d+|\?) EVENTS\]')

RE_FUNC_HEAD = re.compile(r'([A-Za-z_$][\w.$]*)(?: \[([\w-]+)\])?\(')
RE_CALLER = re.compile(r'<-([A-Za-z_$][\w.$]*)\+(0x[0-9a-fA-F]+)/(0x[0-9a-fA-F]+)(?: \[([\w-]+)\])?')
# kernels with BTF-aware funcgraph-retval also print ret=true/false, unsigned decimals and '(trunc)'
RE_RET = re.compile(r' ret=(0[xX][0-9a-fA-F]+|-?\d+|true|false)(\(trunc\))?(?=\s|\*/|$)')
RE_EXIT_NAME = re.compile(r'^ ([A-Za-z_$][\w.$]*)(?: \[([\w-]+)\])?')
# preempt-depth digit of task-context lines (for the baseline of the trace)
RE_LAT_TASK = re.compile(r' [.dDb][.BNLbnpl]\.([.0-9a-f])[.0-9a-f]? \| ')
# Functions that may sleep: calling them with irqs off, preemption disabled or from an
# interrupt is a bug ("BUG: sleeping function called from invalid context")
RE_MAY_SLEEP = re.compile(r'^(?:__might_sleep|__might_fault|mutex_lock(?:_nested|_interruptible\w*|_killable\w*|_io\w*)?|__mutex_lock|'
                          r'down(?:_read|_write|_interruptible|_killable|_timeout)?(?:_nested)?|rwsem_down_\w+_slowpath|'
                          r'wait_for_completion\w*|msleep\w*|usleep_range\w*|schedule|io_schedule|schedule_timeout\w*|'
                          r'schedule_hrtimeout\w*|synchronize_rcu\w*)$')
SEC_IRQ, SEC_PREEMPT, SEC_BH = 0, 1, 2
RE_OVERHEAD = re.compile(r'^# fgcapture: overhead child=(\d+)ns self=(\d+)ns')
RE_SCHED_SWITCH = re.compile(r'sched_switch: prev_comm=(.*?) prev_pid=(\d+) .*?prev_state=(\S+) ==> next_comm=(.*?) next_pid=(\d+)')
# sched_waking fires in the waker's context; sched_wakeup may fire on the target CPU (TTWU_QUEUE IPI)
RE_SCHED_WAKEUP = re.compile(r'sched_(waking|wakeup|wakeup_new): comm=(.*?) pid=(\d+)')

# hardirq/softirq char -> context code
CTX_TASK, CTX_SOFTIRQ, CTX_HARDIRQ, CTX_NMI = 0, 1, 2, 3
_CTX_CHAR = {'.': CTX_TASK, 's': CTX_SOFTIRQ, 'h': CTX_HARDIRQ, 'H': CTX_HARDIRQ, 'z': CTX_NMI, 'Z': CTX_NMI}
_RESCHED_CODE = {'.': 0, 'n': 1, 'N': 2, 'p': 3, 'l': 4, 'L': 5, 'b': 6, 'B': 7}

# Slice flag bits (shared with timeline.js)
F_CTX_MASK = 0x3
F_IRQOFF = 1 << 2
F_BHOFF = 1 << 3
F_RESCHED_SHIFT = 4         # 3 bits
F_PREEMPT_SHIFT = 7         # 4 bits
F_MIGRATE_SHIFT = 11        # 4 bits
F_HAS_LAT = 1 << 15
F_TRUNCATED = 1 << 16       # entry seen, exit missing
F_ORPHAN = 1 << 17          # exit seen, entry missing
F_ESTIMATED = 1 << 18       # duration inferred from timestamps
F_CALLER_MISMATCH = 1 << 19  # retaddr caller != parent slice
F_NULL_RET = 1 << 20
F_LEAF = 1 << 21
F_CTX_INFERRED = 1 << 22
F_USER = 1 << 25
F_BOUNDARY = 1 << 26

# Context inference without latency-format (name based, inherited by callees)
RE_HARDIRQ_ROOT = re.compile(
    r'^(?:__)?sysvec_|^(?:gic|gic_v3|gicv3|aic|plic|riscv_intc)_handle_irq$|^handle_(?:arch|domain)_irq|'
    r'^generic_handle_(?:domain_)?irq|^do_interrupt_handler$|^common_interrupt$|^__common_interrupt$|'
    r'^el[01]_interrupt$|^handle_irq_event$|^do_IRQ$')
RE_SOFTIRQ_ROOT = re.compile(r'^(?:handle_softirqs|__do_softirq|do_softirq|__irq_exit_rcu_softirq)$')
RE_NMI_ROOT = re.compile(r'^(?:exc_nmi|default_do_nmi|arm64_enter_nmi|do_nmi|nmi_handle)$')

RE_SCHED_INTERNAL = re.compile(r'^(?:__)?(?:schedule|preempt_schedule|io_schedule|schedule_[\w]*|__schedule_loop|context_switch|__switch_to|finish_task_switch)')
RE_PREEMPT_PATH = re.compile(r'preempt_schedule|irqentry_exit|exit_to_user_mode_loop|el[01]_interrupt|raw_irqentry_exit_cond_resched|exit_to_kernel_mode|arm64_preempt_schedule_irq')
# Comparators (bsearch/sort callbacks) return a signed difference, not an errno
RE_COMPARATOR = re.compile(r'(?:^|_)(?:cmp|compare|comparator)(?:_|$)|cmp\d*$')
RE_SYSCALL = re.compile(r'^(?:__(?:arm64|x64|ia32|x32|riscv|s390x?|powerpc|loongarch|se)_)?(?:compat_)?sys_\w+$|^__do_sys_\w+$|^__se_sys_\w+$')


def _strip_suffix(name):
    return name.split('.', 1)[0] if name else name


def _parse_dur(s):
    # "2.093" -> 2093 ns ; "1234567" -> 1234567000 ns
    if '.' in s:
        a, b = s.split('.', 1)
        b = (b + '000')[:3]
        return int(a) * 1000 + int(b)
    return int(s) * 1000


class _Frame:
    __slots__ = ('idx', 'depth', 'name', 'ctx')

    def __init__(self, idx, depth, name, ctx):
        self.idx = idx
        self.depth = depth
        self.name = name
        self.ctx = ctx


class _Task:
    __slots__ = ('key', 'tid', 'pid', 'comm', 'comms', 'kind', 'cpu', 'stack', 'vclock',
                 'pending_leaf', 'last_ts', 'last_cpu', 'runs', 'resched_open', 'resched_info',
                 'irqoff_open', 'off_open', 'loose', 'first_ts', 'new_run', 'sec_open', 'last_task_ts')

    def __init__(self, key, tid, pid, comm, kind, cpu):
        self.key = key
        self.tid = tid
        self.pid = pid
        self.comm = comm
        self.comms = [comm] if comm else []
        self.kind = kind
        self.cpu = cpu
        self.stack = []
        self.vclock = 0
        self.pending_leaf = -1
        self.last_ts = None
        self.first_ts = None
        self.last_cpu = None
        self.runs = []            # [start, end, cpu]
        self.resched_open = None  # start ts
        self.resched_info = None  # [max_preempt, irqoff_seen, start_line]
        self.irqoff_open = None
        self.off_open = None      # pending off-cpu record (index into offcpu)
        self.loose = []           # slice indices at unknown parent (orphan adoption)
        self.new_run = False      # next timestamp starts a new on-CPU run (after switch-in)
        self.sec_open = [None, None, None]   # per SEC_* kind: [start_ts, start_slice, start_line, max_depth]
        self.last_task_ts = None             # last timestamp seen in task context


class TimelineBuilder:
    """Build the timeline model from a list of raw trace lines."""

    def __init__(self, lines, rettypes=None, errno_map=None, verbose=False, overhead=None, rettype_resolver=None):
        """Resolve return types from paired module names before error analysis.

        overhead: (child_ns, self_ns), or None to use a fgcapture overhead header.
        rettype_resolver: optional callable accepting this model's module names.
        """
        self.lines = lines
        self.overhead = overhead
        self.rettypes = rettypes
        if rettypes is not None and rettype_resolver is not None:
            raise ValueError('Use either return types or a return-type resolver, not both')
        self.rettype_resolver = rettype_resolver
        self.errno_map = errno_map or {}
        self.verbose = verbose

        # slice columns (python lists, parallel)
        self.s_task, self.s_depth, self.s_start, self.s_end = [], [], [], []
        self.s_name, self.s_line, self.s_eline, self.s_flags = [], [], [], []
        self.s_parent, self.s_ret, self.s_caller, self.s_args = [], [], [], []
        self.s_module = []
        self.s_cpu = []

        self.tasks = {}
        self.task_list = []
        self.strings = []
        self._str_idx = {}

        self.cpu_current = {}     # cpu -> task key (from switch markers)
        self.cpu_last_ts = {}
        self.cpu_irq_depth = {}   # cpu -> nesting count of ==========> markers
        self.pending_switch = {}  # cpu -> (prev_key, next_key)
        self.pending_state = {}   # pid -> prev_state from sched_switch
        self.offcpu = []          # [task, start, end, depth, reason_idx, state_idx, cpu_out, cpu_in, preempted, waker_idx]
        self.resched = []         # [task, start, end, max_preempt, irqoff, closed_by]
        self.irqoff = []          # [task, start, end]
        self.sections = []        # [task, kind, start, end, start_slice, end_slice, max_depth, start_line, end_line]
        self.atomic = []          # [slice, reasons bits: 1 irqs off, 2 preempt off, 4 irq context, 8 BH off]
        self.preempt_base = 0
        self.lost = []            # [cpu, ts, count, line]
        self.wakeups = {}         # pid -> list of (ts, waker_task)
        self.wake_events = []     # [waker_tid, ts, target_tid, ctx, waker_slice, offcpu_rec|None]

        self.caps = dict(abs=False, rel=False, cpu=False, proc=False, latency=False, duration=False,
                         retval=False, retaddr=False, tail=False, args=False, irq_markers=False,
                         sched_events=False, sched_waking=False, switch_markers=False, retval_btf=False)
        self.mode = None
        self.overhead_source = 'option' if overhead else None
        self.stats = {}
        self._cur_cpu = -1

    # -- helpers -----------------------------------------------------
    def _s(self, text, namespace=None):
        if text is None:
            return -1
        key = (namespace, text) if namespace is not None else text
        i = self._str_idx.get(key)
        if i is None:
            i = len(self.strings)
            self.strings.append(text)
            self._str_idx[key] = i
        return i

    def _task(self, pid, comm, cpu, namespace=None):
        if pid is not None:
            if pid == 0:
                key = ('idle', cpu)
            else:
                key = pid
        elif cpu is not None and cpu in self.cpu_current:
            key = self.cpu_current[cpu]
        elif cpu is not None:
            key = ('cpu', cpu)
        else:
            key = ('all',)
        original_key = key
        if namespace is not None:
            key = (namespace, key)
        t = self.tasks.get(key)
        if t is None:
            if isinstance(original_key, tuple):
                kind = {'idle': 1, 'cpu': 2, 'all': 3}[original_key[0]]
                kc = original_key[1] if len(original_key) > 1 else None
                kpid = 0 if kind == 1 else -1
                kcomm = comm if comm else ('<idle>' if kind == 1 else ('CPU %d' % kc if kind == 2 else 'all'))
            else:
                kind, kc, kpid, kcomm = 0, None, original_key, comm or ('pid %d' % original_key)
            if namespace is not None:
                kcomm += ' · ' + ('user' if namespace == 'user' else 'kernel, unjoined')
            t = _Task(key, len(self.task_list), kpid, kcomm, kind, kc)
            self.tasks[key] = t
            self.task_list.append(t)
        elif namespace is None and comm and t.kind == 0 and comm != t.comm and not comm.startswith('<...'):
            t.comm = comm
            if comm not in t.comms:
                t.comms.append(comm)
        return t

    def _new_slice(self, task, depth, start, end, name, module, line, flags, parent, ret, caller, args, namespace=None):
        idx = len(self.s_task)
        self.s_task.append(task.tid)
        self.s_depth.append(depth)
        self.s_start.append(start)
        self.s_end.append(end)
        self.s_name.append(self._s(name, namespace))
        self.s_module.append(module)
        self.s_line.append(line)
        self.s_eline.append(0)
        self.s_flags.append(flags)
        self.s_parent.append(parent)
        self.s_ret.append(ret)
        self.s_caller.append(caller)
        self.s_args.append(args)
        self.s_cpu.append(self._cur_cpu)
        if end is not None and task.runs and end > task.runs[-1][1]:
            task.runs[-1][1] = end
        return idx

    # -- prologue ------------------------------------------------------
    def _prologue(self, line):
        """Return (ts_ns, cpu, comm, pid, lat, dur_ns, has_dur_col, pos)."""
        pos = 0
        ts = cpu = comm = pid = lat = dur = None
        has_dur_col = False
        m = RE_ABS.match(line, pos)
        if m:
            ts = int(m.group(1)) * 1000000000 + int(m.group(2).ljust(9, '0'))
            self.caps['abs'] = True
            pos = m.end()
        else:
            m = RE_REL.match(line, pos)
            if m:
                ts = int(m.group(1)) * 1000
                self.caps['rel'] = True
                pos = m.end()
        m = RE_CPU.match(line, pos)
        if m:
            cpu = int(m.group(1))
            pos = m.end()
        m = RE_PROC.match(line, pos)
        if m and not RE_LAT.match(line, pos):
            comm = m.group(1).strip()
            pid = int(m.group(2))
            pos = m.end()
        m = RE_LAT.match(line, pos)
        if m:
            lat = m.group(1)
            pos = m.end()
        m = RE_DUR.match(line, pos) or RE_DUR_NOMARK.match(line, pos)
        if m:
            dur = _parse_dur(m.group(m.lastindex))
            has_dur_col = True
            pos = m.end()
        else:
            m = RE_FILL.match(line, pos)
            if m:
                has_dur_col = True
                pos = m.end()
        return ts, cpu, comm, pid, lat, dur, has_dur_col, pos

    @staticmethod
    def _lat_flags(lat):
        if not lat:
            return 0, None
        f = F_HAS_LAT
        c0 = lat[0]
        if c0 in 'dD':
            f |= F_IRQOFF
        if c0 in 'bD':
            f |= F_BHOFF
        f |= _RESCHED_CODE.get(lat[1], 0) << F_RESCHED_SHIFT
        ctx = _CTX_CHAR.get(lat[2], 0)
        f |= ctx
        if lat[3] != '.':
            f |= (int(lat[3], 16) & 0xf) << F_PREEMPT_SHIFT
        if len(lat) > 4 and lat[4] != '.':
            f |= (int(lat[4], 16) & 0xf) << F_MIGRATE_SHIFT
        return f, ctx

    # -- main pass -----------------------------------------------------
    def _preempt_baseline(self):
        """Preempt depth the tracer itself adds to every record (0 or 1).

        function_graph records tracing_gen_ctx() while its own entry path runs with preemption
        disabled, so on CONFIG_PREEMPTION kernels every line shows one level too many (other
        tracers use tracing_gen_ctx_dec() for this). Measured as the lowest depth seen in task
        context, capped at 1: a trace that lies entirely inside a preempt-off region (lowest 2+)
        must not hide that region."""
        lo = None
        for raw in self.lines:
            m = RE_LAT_TASK.search(raw, 0, 120)
            if m:
                c = m.group(1)
                v = 0 if c == '.' else int(c, 16)
                if lo is None or v < lo:
                    lo = v
                    if v == 0:
                        break
        return min(lo or 0, 1)

    def build(self):
        t_start = time.time()
        lines = self.lines
        self.preempt_base = self._preempt_baseline()
        ts_seen = False
        for lineno0, raw in enumerate(lines):
            if not raw or raw[0] == '#':
                if raw and self.overhead is None and raw.startswith('# fgcapture: overhead'):
                    m = RE_OVERHEAD.match(raw)
                    if m:
                        self.overhead = (int(m.group(1)), int(m.group(2)))
                        self.overhead_source = 'trace header'
                continue
            lineno = lineno0 + 1
            st = raw.lstrip()
            if not st:
                continue
            if st.startswith('-----'):
                continue
            if st.startswith('CPU:'):
                m = RE_LOST.match(st)
                if m:
                    count = None if m.group(2) == '?' else int(m.group(2))
                    self.lost.append([int(m.group(1)), None, count, lineno])
                continue
            if st.startswith('#####') or st.startswith('(Overruns'):
                continue
            if '=>' in raw and '|' not in raw:
                m = RE_SWITCH.match(raw)
                if m:
                    self._on_switch(m)
                    continue

            ts, cpu, comm, pid, lat, dur, has_dur_col, pos = self._prologue(raw)
            body = raw[pos:]
            if cpu is not None:
                self.caps['cpu'] = True
            if pid is not None:
                self.caps['proc'] = True
            if lat is not None:
                self.caps['latency'] = True
            if has_dur_col:
                self.caps['duration'] = True
            if ts is not None:
                ts_seen = True

            stripped = body.strip()
            if not stripped:
                continue
            if stripped.startswith('==========>') or stripped.startswith('<=========='):
                self.caps['irq_markers'] = True
                if cpu is not None:
                    d = self.cpu_irq_depth.get(cpu, 0)
                    self.cpu_irq_depth[cpu] = d + 1 if stripped[0] == '=' else max(0, d - 1)
                continue

            self._cur_cpu = cpu if cpu is not None else -1
            task = self._task(pid, comm, cpu)
            self._resolve_switch_in(task, cpu, ts)
            prev_ts = task.last_ts
            if ts is not None:
                self._track_run(task, cpu, ts)
                if cpu is not None:
                    self.cpu_last_ts[cpu] = ts
            lflags, lctx = self._lat_flags(lat)
            if lat is not None and ts is not None:
                # trace event lines (/* sched_switch: ... */) are printed from inside the scheduler or the
                # event code with irqs off: they say nothing about the task's own critical sections
                self._track_lat(task, lat, lflags, ts, lineno, prev_ts, event=stripped.startswith('/*'))
            # Fill a pending leaf duration (no duration column) from the next timestamp
            if task.pending_leaf >= 0 and ts is not None:
                i = task.pending_leaf
                self.s_end[i] = max(ts, self.s_start[i] + 1)
                task.pending_leaf = -1

            indent = len(body) - len(body.lstrip(' '))
            depth = indent // 2

            if stripped.startswith('/*'):
                self._on_comment(stripped, task, ts, cpu, lctx)
                continue
            if stripped[0] == '}':
                self._on_exit(task, depth, stripped, ts, dur, lineno, lflags)
                continue
            m = RE_FUNC_HEAD.match(stripped)
            if not m:
                continue
            name, module = m.group(1), m.group(2)
            rest = stripped[m.end():]
            if rest.rstrip().endswith('{') or ') {' in rest:
                kind = 'entry'
                cut = rest.rfind(') {')
                args = rest[:cut] if cut >= 0 else ''
                comment = rest[cut + 3:] if cut >= 0 else ''
            else:
                kind = 'leaf'
                cut = rest.rfind(');')
                args = rest[:cut] if cut >= 0 else rest.rstrip(');')
                comment = rest[cut + 2:] if cut >= 0 else ''
            caller = None
            ret = None
            if comment:
                mc = RE_CALLER.search(comment)
                if mc:
                    caller = mc.group(1) + '+' + mc.group(2) + '/' + mc.group(3) + (' [' + mc.group(4) + ']' if mc.group(4) else '')
                    self.caps['retaddr'] = True
                mr = RE_RET.search(comment)
                if mr:
                    ret = self._ret_value(mr)
            if args:
                self.caps['args'] = True
            self._on_call(kind, task, depth, name, module, args or None, caller, ret, ts, dur, lineno, lflags, lctx, cpu)

        self._finish(ts_seen)
        self.stats['build_time'] = time.time() - t_start
        return self

    # -- events ---------------------------------------------------------
    def _close_stale(self, task, depth, ts):
        """Close frames at depth >= `depth` whose exit was lost."""
        st = task.stack
        while st and st[-1].depth >= depth:
            fr = st.pop()
            i = fr.idx
            self.s_flags[i] |= F_TRUNCATED
            if self.s_end[i] is None:
                self.s_end[i] = ts if ts is not None else self.s_start[i]

    def _ctx_for(self, task, name, lctx, cpu):
        """Return (ctx, inferred_flag) for a new frame."""
        if lctx is not None:
            return lctx, 0
        parent_ctx = task.stack[-1].ctx if task.stack else CTX_TASK
        ctx = parent_ctx
        base = _strip_suffix(name)
        if RE_NMI_ROOT.match(base):
            ctx = CTX_NMI
        elif RE_HARDIRQ_ROOT.match(base) and ctx < CTX_HARDIRQ:
            ctx = CTX_HARDIRQ
        elif RE_SOFTIRQ_ROOT.match(base) and ctx < CTX_SOFTIRQ:
            ctx = CTX_SOFTIRQ
        if cpu is not None and self.cpu_irq_depth.get(cpu, 0) > 0 and ctx < CTX_HARDIRQ:
            ctx = CTX_HARDIRQ
        return ctx, F_CTX_INFERRED

    def _on_call(self, kind, task, depth, name, module, args, caller, ret, ts, dur, lineno, lflags, lctx, cpu):
        self._close_stale(task, depth, ts)
        # Parent = innermost open frame. Usually one level up; the kernel's depth can also jump by
        # more (a frame whose entry was not recorded, e.g. arch_stack_walk, el1_interrupt on arm64):
        # the call still belongs to the enclosing frame, the front end draws the missing level(s).
        parent = task.stack[-1].idx if task.stack and task.stack[-1].depth < depth else -1
        ctx, inferred = self._ctx_for(task, name, lctx, cpu)
        flags = (lflags & ~F_CTX_MASK) | ctx | inferred
        if ts is not None:
            start = ts
        else:
            start = task.vclock
        if caller and parent >= 0:
            pname = self.strings[self.s_name[parent]]
            if _strip_suffix(caller.split('+', 1)[0]) != _strip_suffix(pname):
                flags |= F_CALLER_MISMATCH
        args_i = self._s(args) if args else -1
        caller_i = self._s(caller) if caller else -1
        atomic = 0
        if lflags & F_HAS_LAT and RE_MAY_SLEEP.match(_strip_suffix(name)):
            if lflags & F_IRQOFF:
                atomic |= 1
            if ((lflags >> F_PREEMPT_SHIFT) & 0xf) > self.preempt_base:
                atomic |= 2
            if ctx != CTX_TASK:
                atomic |= 4
            if lflags & F_BHOFF:
                atomic |= 8
        if kind == 'leaf':
            flags |= F_LEAF
            if dur is not None:
                end = start + dur
            elif ts is not None:
                end = None
                flags |= F_ESTIMATED
            else:
                end = start + 1000
                flags |= F_ESTIMATED
            idx = self._new_slice(task, depth, start, end, name, module, lineno, flags, parent, ret, caller_i, args_i)
            if atomic:
                self.atomic.append([idx, atomic])
            if end is None:
                self.s_end[idx] = start + 1
                task.pending_leaf = idx
            if ts is None:
                task.vclock = self.s_end[idx]
            if parent < 0:
                task.loose.append(idx)
        else:
            idx = self._new_slice(task, depth, start, None, name, module, lineno, flags, parent, None, caller_i, args_i)
            if atomic:
                self.atomic.append([idx, atomic])
            task.stack.append(_Frame(idx, depth, name, ctx))
            if parent < 0:
                task.loose.append(idx)

    def _on_exit(self, task, depth, stripped, ts, dur, lineno, lflags):
        name = module = ret = None
        cm = stripped.find('/*')
        if cm >= 0:
            comment = stripped[cm + 2:].rstrip()
            if comment.endswith('*/'):
                comment = comment[:-2]
            mn = RE_EXIT_NAME.match(comment)
            if mn:
                name, module = mn.group(1), mn.group(2)
                self.caps['tail'] = True
            mr = RE_RET.search(comment)
            if mr:
                ret = self._ret_value(mr)
        st = task.stack
        target = None
        if st and st[-1].depth == depth and (name is None or st[-1].name == name):
            target = len(st) - 1
        elif name is not None:
            for k in range(len(st) - 1, -1, -1):
                if st[k].depth == depth and st[k].name == name:
                    target = k
                    break
                if st[k].depth < depth:
                    break
        elif st and st[-1].depth > depth:
            # Exits of deeper frames were lost; match by depth
            for k in range(len(st) - 1, -1, -1):
                if st[k].depth == depth:
                    target = k
                    break
        if target is not None:
            while len(st) - 1 > target:
                fr = st.pop()
                self.s_flags[fr.idx] |= F_TRUNCATED
                if self.s_end[fr.idx] is None:
                    self.s_end[fr.idx] = ts if ts is not None else self.s_start[fr.idx]
            fr = st.pop()
            i = fr.idx
            start = self.s_start[i]
            if dur is not None:
                end = start + dur
            elif ts is not None:
                end = max(ts, start)
                self.s_flags[i] |= F_ESTIMATED
            else:
                end = max(task.vclock, start + 1000)
                self.s_flags[i] |= F_ESTIMATED
            self.s_end[i] = end
            self.s_ret[i] = ret
            self.s_eline[i] = lineno
            if ts is not None and task.runs and end > task.runs[-1][1]:
                task.runs[-1][1] = end
            if ts is None:
                task.vclock = max(task.vclock, end)
            return
        # Orphan exit: the entry happened before the capture window (or was lost)
        if name is None:
            name = '?'
        self._close_stale(task, depth + 0, ts) if st and st[-1].depth > depth else None
        if ts is not None:
            end = ts
            start = ts - dur if dur is not None else None
        else:
            end = task.vclock
            start = end - dur if dur is not None else None
        kids = [i for i in task.loose if self.s_depth[i] == depth + 1 and self.s_parent[i] < 0]
        if start is None:
            start = min([self.s_start[i] for i in kids] + [end])
        if kids:
            start = min(start, min(self.s_start[i] for i in kids))
        flags = F_ORPHAN | (lflags & ~F_CTX_MASK) | (lflags & F_CTX_MASK)
        parent = st[-1].idx if st and st[-1].depth == depth - 1 else -1
        idx = self._new_slice(task, depth, start, end, name, module, lineno, flags, parent, ret, -1, -1)
        self.s_eline[idx] = lineno
        for k in kids:
            self.s_parent[k] = idx
        task.loose = [i for i in task.loose if self.s_parent[i] < 0]
        if parent < 0:
            task.loose.append(idx)

    def _ret_value(self, m):
        self.caps['retval'] = True
        v = m.group(1)
        if v in ('true', 'false') or m.group(2):
            # only the BTF-aware formatter prints booleans and '(trunc)': the kernel already used the
            # return type (void functions print nothing, values are truncated/sign-extended to the type)
            self.caps['retval_btf'] = True
        return v + (m.group(2) or '')

    def _on_comment(self, stripped, task, ts, cpu, lctx=None):
        m = RE_SCHED_SWITCH.search(stripped)
        if m:
            self.caps['sched_events'] = True
            self.pending_state[int(m.group(2))] = m.group(3)
            return
        m = RE_SCHED_WAKEUP.search(stripped)
        if m and ts is not None:
            self.caps['sched_events'] = True
            kind, pid = m.group(1), int(m.group(3))
            if kind == 'waking':
                self.caps['sched_waking'] = True
            elif self.caps.get('sched_waking'):
                return          # the waking event already identified the real waker
            self.wakeups.setdefault(pid, []).append((ts, task.tid))
            target = self._task(pid, m.group(2).strip(), None) if pid else None
            if target is not None:
                ctx = lctx if lctx is not None else (task.stack[-1].ctx if task.stack else CTX_TASK)
                waker_slice = task.stack[-1].idx if task.stack else -1
                self.wake_events.append([task.tid, ts, target.tid, ctx, waker_slice, target.off_open])

    def _on_switch(self, m):
        self.caps['switch_markers'] = True
        cpu = int(m.group(1)) if m.group(1) else None
        prev_pid, next_pid = int(m.group(3)), int(m.group(5))
        self._switch(cpu, prev_pid, next_pid, m.group(2).strip(), m.group(4).strip())

    def _switch(self, cpu, prev_pid, next_pid, prev_comm, next_comm, timestamp=None):
        prev = self._task(prev_pid, prev_comm, cpu)
        nxt = self._task(next_pid, next_comm, cpu)
        if cpu is not None:
            self.cpu_current[cpu] = nxt.key
        # Switch-out time: last timestamp of prev (on this CPU)
        t_out = prev.last_ts if timestamp is None else timestamp
        if prev.kind == 0 and t_out is not None:
            # stack at switch-out explains why the task left the CPU
            names = [self.strings[self.s_name[f.idx]] for f in prev.stack]
            reason = None
            preempted = any(RE_PREEMPT_PATH.search(n) for n in names)
            for n in reversed(names):
                if not RE_SCHED_INTERNAL.match(n):
                    reason = n
                    break
            state = self.pending_state.pop(prev_pid, None)
            if state is not None:
                preempted = state.startswith('R')
            depth = prev.stack[-1].depth + 1 if prev.stack else 0
            rec = [prev.tid, t_out, None, depth, self._s(reason), self._s(state), cpu, None,
                   1 if preempted else 0, -1]
            self.offcpu.append(rec)
            prev.off_open = rec
        if prev.resched_open is not None and t_out is not None:
            info = prev.resched_info
            self.resched.append([prev.tid, prev.resched_open, t_out, info[0], info[1], 1])
            prev.resched_open = None
        if t_out is not None:
            self._close_sections(prev, t_out, -1)
        if prev.irqoff_open is not None and t_out is not None:
            self.irqoff.append([prev.tid, prev.irqoff_open, t_out])
            prev.irqoff_open = None
        if cpu is not None:
            self.pending_switch[cpu] = nxt.key

    def _resolve_switch_in(self, task, cpu, ts):
        if task.off_open is not None and ts is not None:
            rec = task.off_open
            rec[2] = ts
            rec[7] = cpu
            # who woke us up?
            wl = self.wakeups.get(task.pid)
            if wl:
                for wts, wtid in reversed(wl):
                    if rec[1] <= wts <= ts:
                        rec[9] = wtid
                        break
            task.off_open = None
            task.new_run = True

    def _track_run(self, task, cpu, ts):
        if task.first_ts is None:
            task.first_ts = ts
        runs = task.runs
        if runs and runs[-1][2] == cpu and task.off_open is None and not task.new_run:
            if ts > runs[-1][1]:
                runs[-1][1] = ts
        else:
            runs.append([ts, ts, cpu])
        task.new_run = False
        task.last_ts = ts
        task.last_cpu = cpu

    def _track_lat(self, task, lat, lflags, ts, lineno, prev_ts=None, event=False):
        if lat[1] != '.':
            if task.resched_open is None:
                task.resched_open = ts
                task.resched_info = [0, 0, lineno]
            info = task.resched_info
            p = max(0, ((lflags >> F_PREEMPT_SHIFT) & 0xf) - self.preempt_base)   # minus the tracer's own level
            if p > info[0]:
                info[0] = p
            if lflags & F_IRQOFF:
                info[1] = 1
        elif task.resched_open is not None:
            info = task.resched_info
            self.resched.append([task.tid, task.resched_open, ts, info[0], info[1], 0])
            task.resched_open = None
        if event:
            return
        ctx = lflags & F_CTX_MASK
        # The task was switched out inside __schedule (no switch marker without funcgraph-proc
        # or with set_ftrace_pid): end its sections when it left, a new one starts on resume.
        if (prev_ts is not None and ts - prev_ts > 20000 and any(task.sec_open)
                and any(RE_SCHED_INTERNAL.match(self.strings[self.s_name[f.idx]]) for f in task.stack[-4:])):
            self._close_sections(task, prev_ts, lineno)
        if ctx in (CTX_SOFTIRQ, CTX_HARDIRQ) and task.last_task_ts is not None:
            # An interrupt (or softirq on irq exit) ran, so irqs - and for softirq BH - were enabled
            # again before it, in code that was not traced: end the section at the last task-context line.
            for kind in ((SEC_IRQ, SEC_BH) if ctx == CTX_SOFTIRQ else (SEC_IRQ,)):
                op = task.sec_open[kind]
                if op is not None:
                    self.sections.append([task.tid, kind, op[0], task.last_task_ts, op[1], -1, op[3], op[2], lineno])
                    task.sec_open[kind] = None
        if ctx == CTX_TASK and task.kind == 0:
            task.last_task_ts = ts
            pd = ((lflags >> F_PREEMPT_SHIFT) & 0xf) - self.preempt_base
            frame = task.stack[-1].idx if task.stack else -1
            for kind, on, depth in ((SEC_IRQ, lflags & F_IRQOFF, 1), (SEC_PREEMPT, pd > 0, pd),
                                    (SEC_BH, lflags & F_BHOFF, 1)):
                op = task.sec_open[kind]
                if on:
                    if op is None:
                        task.sec_open[kind] = [ts, frame, lineno, depth]
                    elif depth > op[3]:
                        op[3] = depth
                elif op is not None:
                    self.sections.append([task.tid, kind, op[0], ts, op[1], frame, op[3], op[2], lineno])
                    task.sec_open[kind] = None
        if (lflags & F_IRQOFF) and ctx == CTX_TASK:
            if task.irqoff_open is None:
                task.irqoff_open = ts
        elif task.irqoff_open is not None and ctx == CTX_TASK:
            self.irqoff.append([task.tid, task.irqoff_open, ts])
            task.irqoff_open = None

    def _close_sections(self, task, ts, line):
        for kind, op in enumerate(task.sec_open):
            if op is not None:
                self.sections.append([task.tid, kind, op[0], ts, op[1], -1, op[3], op[2], line])
                task.sec_open[kind] = None

    # -- post-processing ----------------------------------------------
    def _finish(self, ts_seen):
        c = self.caps
        if c['abs']:
            self.mode = 'abs'
        elif c['rel']:
            self.mode = 'rel'
        elif c['duration']:
            self.mode = 'compressed'
        else:
            self.mode = 'count'
        n = len(self.s_task)
        # close remaining frames
        trunc_open = 0
        for t in self.task_list:
            end_ts = t.last_ts if t.last_ts is not None else t.vclock
            for fr in t.stack:
                i = fr.idx
                self.s_flags[i] |= F_TRUNCATED
                if self.s_end[i] is None:
                    self.s_end[i] = max(end_ts if end_ts is not None else self.s_start[i], self.s_start[i])
                trunc_open += 1
            t.stack = []
            if t.resched_open is not None and t.last_ts is not None:
                self.resched.append([t.tid, t.resched_open, t.last_ts, t.resched_info[0], t.resched_info[1], 2])
            if t.last_ts is not None:
                self._close_sections(t, t.last_ts, -1)
            if t.irqoff_open is not None and t.last_ts is not None:
                self.irqoff.append([t.tid, t.irqoff_open, t.last_ts])
        # Truncated frames: end must cover their children
        children = [[] for _ in range(n)]
        for i in range(n):
            p = self.s_parent[i]
            if p >= 0:
                children[p].append(i)
        order = sorted(range(n), key=lambda i: -self.s_depth[i])
        for i in order:
            if self.s_flags[i] & F_TRUNCATED and children[i]:
                m = max(self.s_end[k] for k in children[i])
                if m > self.s_end[i]:
                    self.s_end[i] = m
        drift_fix = 0
        # Parentless slices in the same row: trim the earlier one (timestamp rounding / lost events)
        roots = {}
        for i in range(n):
            if self.s_parent[i] < 0:
                roots.setdefault((self.s_task[i], self.s_depth[i]), []).append(i)
        for idx in roots.values():
            # trace order decides between equal (us-rounded) timestamps; the later call is shifted
            idx.sort(key=lambda i: (self.s_start[i], self.s_line[i]))
            prev_end = None
            for i in idx:
                s0, e0 = self.s_start[i], self.s_end[i]
                if prev_end is not None and s0 < prev_end:
                    self.s_start[i] = prev_end
                    self.s_end[i] = prev_end + (e0 - s0)
                    drift_fix += 1
                prev_end = self.s_end[i]
        # Clamp children into parents and remove sibling overlap (timestamp rounding)
        for i in sorted(range(n), key=lambda i: self.s_depth[i]):
            kids = children[i]
            if not kids:
                continue
            ps, pe = self.s_start[i], self.s_end[i]
            kids.sort(key=lambda k: self.s_start[k])
            prev_end = ps
            for k in kids:
                s, e = self.s_start[k], self.s_end[k]
                d = e - s
                if s < prev_end:
                    s = prev_end
                    drift_fix += 1
                e = s + d
                if e > pe:
                    e = pe
                    if s > e:
                        s = e
                    drift_fix += 1
                self.s_start[k], self.s_end[k] = s, e
                prev_end = e
        # Error-path analysis
        if self.rettype_resolver is not None:
            self.rettypes = self.rettype_resolver(self.s_module)
        self._analyze_errors(children)
        self.stats.update(slices=n, tasks=len(self.task_list), truncated=sum(1 for f in self.s_flags if f & F_TRUNCATED),
                          orphans=sum(1 for f in self.s_flags if f & F_ORPHAN), clamped=drift_fix,
                          offcpu=len(self.offcpu), resched=len(self.resched), lost=len(self.lost))
        self._children = children

    def _err_value(self, i):
        """Return (errno or None, verified, is_null)."""
        r = self.s_ret[i]
        if r is None:
            return None, False, False
        if r in ('true', 'false') or r.endswith('(trunc)'):
            return None, True, False
        try:
            v = int(r, 0)
        except ValueError:
            return None, False, False
        if self.caps.get('retval_btf') and self.rettypes is None:
            # type-aware kernel formatting: errnos (signed types, ERR_PTR) are printed as negative
            # decimals, everything else (pointers, unsigned) as hex or unsigned decimal
            if r.startswith('-') and -4095 <= v <= -1:
                return v, True, False
            return None, True, False
        if v < 0:
            v &= (1 << 64) - 1
        name = self.strings[self.s_name[i]]
        cls = None
        if self.rettypes is not None:
            res, _clone = self.rettypes.lookup(name)
            if res is not None:
                cls, size = res
        if cls is None:
            # unverified: decimal negative output means the kernel saw an IS_ERR_VALUE
            s32 = v & 0xffffffff
            s32 = s32 - (1 << 32) if s32 & 0x80000000 else s32
            s64 = v - (1 << 64) if v & (1 << 63) else v
            if -4095 <= s64 <= -1:
                return s64, False, False
            if (v >> 32) == 0 and -4095 <= s32 <= -1:
                return s32, False, False
            return None, False, False
        if cls == 'sint':
            if RE_COMPARATOR.search(_strip_suffix(name)):
                return None, True, False
            bits = max(1, size) * 8
            x = v & ((1 << bits) - 1)
            if x & (1 << (bits - 1)):
                x -= 1 << bits
            return (x, True, False) if -4095 <= x <= -1 else (None, True, False)
        if cls == 'ptr':
            if v == 0:
                return None, True, True
            x = v - (1 << 64) if v & (1 << 63) else v
            return (x, True, False) if -4095 <= x <= -1 else (None, True, False)
        return None, True, False

    def _analyze_errors(self, children):
        n = len(self.s_task)
        err = [None] * n
        verified = [False] * n
        for i in range(n):
            e, ver, isnull = self._err_value(i)
            err[i] = e
            verified[i] = ver
            if isnull:
                self.s_flags[i] |= F_NULL_RET
        self.err_rows = []    # [slice, errno, role, fate, verified, chain]
        self.chains = []      # [origin, top, errno, fate, length]
        chain_of = {}
        for i in range(n):
            e = err[i]
            if e is None:
                continue
            if any(err[k] == e for k in children[i]):
                continue  # not an origin
            # origin: walk up while the parent returns the same value
            members = [i]
            top = i
            p = self.s_parent[i]
            while p >= 0 and err[p] == e:
                members.append(p)
                top = p
                p = self.s_parent[p]
            names = [self.strings[self.s_name[k]] for k in members]
            if any(RE_SYSCALL.match(_strip_suffix(nm)) for nm in names):
                fate = 1   # reaches user space
            elif p < 0 or self.s_flags[p] & F_USER:
                fate = 2   # leaves the traced window
            else:
                fate = 0   # handled by the caller
            cid = len(self.chains)
            self.chains.append([i, top, e, fate, len(members), 1 if all(verified[k] for k in members) else 0])
            for k in members:
                chain_of[k] = cid
        # Minor: a single-frame origin handled right away by its caller, by a function whose
        # negative returns never propagate anywhere in the trace. Typically signed values that
        # are not errnos (e.g. memcg_state_val_in_pages page deltas) or normal probing (-ENOEXEC
        # from binfmt handlers). Kept, but separated from the errors that matter.
        propagating = set()
        for c in self.chains:
            if c[4] > 1:
                propagating.add(self.s_name[c[0]])
        for c in self.chains:
            c.append(1 if (c[3] == 0 and c[4] == 1 and self.s_name[c[0]] not in propagating) else 0)
        for i in range(n):
            e = err[i]
            if e is None:
                continue
            cid = chain_of.get(i, -1)
            role = 0 if (cid >= 0 and self.chains[cid][0] == i) else 1
            fate = self.chains[cid][3] if cid >= 0 else 0
            self.err_rows.append([i, e, role, fate, 1 if verified[i] else 0, cid])

    # -- serialization --------------------------------------------------
    def to_data(self):
        n = len(self.s_task)
        timed = self.mode in ('abs', 'rel')
        # Time origin: the earliest timestamp of anything drawn, not only of the calls. Sched events
        # and context switches before the first traced call (an off-CPU period that began earlier,
        # a need-resched window, a wakeup) would otherwise get negative times.
        starts = [min(self.s_start)] if n else []
        if timed:
            starts += [r[1] for r in self.offcpu if r[1] is not None]
            starts += [r[1] for r in self.resched] + [r[1] for r in self.irqoff] + [r[2] for r in self.sections]
            starts += [w[1] for w in self.wake_events if w[1] is not None]
            starts += [a for t in self.task_list for a, b, c in t.runs if a is not None]
        t0 = min(starts) if starts else 0
        # global order: task, start, depth
        order = sorted(range(n), key=lambda i: (self.s_task[i], self.s_start[i], self.s_depth[i]))
        remap = [0] * n
        for new, old in enumerate(order):
            remap[old] = new
        start_d = []
        prev_task, prev_start = -1, 0
        for old in order:
            st = self.s_start[old] - t0
            if self.s_task[old] != prev_task:
                prev_task, prev_start = self.s_task[old], 0
            start_d.append(st - prev_start)
            prev_start = st
        ret_col = []
        for old in order:
            r = self.s_ret[old]
            ret_col.append(self._s(r) if r is not None else -1)
        mod_col = [self._s(self.s_module[old]) if self.s_module[old] else -1 for old in order]
        cols = {
            'task': [self.s_task[o] for o in order],
            'depth': [self.s_depth[o] for o in order],
            'start': start_d,
            'dur': [self.s_end[o] - self.s_start[o] for o in order],
            'name': [self.s_name[o] for o in order],
            'line': [self.s_line[o] for o in order],
            'eline': [self.s_eline[o] for o in order],
            'flags': [self.s_flags[o] for o in order],
            'parent': [remap[self.s_parent[o]] if self.s_parent[o] >= 0 else -1 for o in order],
            'ret': ret_col,
            'caller': [self.s_caller[o] for o in order],
            'args': [self.s_args[o] for o in order],
            'module': mod_col,
            'cpu': [self.s_cpu[o] for o in order],
            'adj': None,
        }
        ovh = self.overhead if self.overhead and any(self.overhead) else None
        if ovh:
            # corrected duration = measured - child_ns per traced descendant - self_ns, never below 0
            desc = [0] * n
            par = cols['parent']
            for i in sorted(range(n), key=lambda i: -cols['depth'][i]):
                p = par[i]
                if p >= 0:
                    desc[p] += 1 + desc[i]
            child_ns, self_ns = ovh
            cols['adj'] = [max(0, d - child_ns * k - self_ns) for d, k in zip(cols['dur'], desc)]
        # sparse columns are dropped when empty
        for k in ('ret', 'caller', 'args', 'module', 'eline', 'cpu'):
            empty = (0,) if k == 'eline' else (-1,)
            if all(v in empty for v in cols[k]):
                cols[k] = None
        tasks = []
        for t in self.task_list:
            tasks.append({'pid': t.pid, 'comm': t.comm, 'comms': t.comms, 'kind': t.kind, 'cpu': t.cpu,
                          'runs': [[a - t0, b - t0, c if c is not None else -1] for a, b, c in t.runs] if self.mode in ('abs', 'rel') else []})
        errnames = {}
        for row in self.err_rows:
            e = row[1]
            if e in self.errno_map:
                errnames[str(e)] = self.errno_map[e]

        def rel(v):
            return (v - t0) if v is not None else None

        off_index = {}
        if timed:
            k = 0
            for r in self.offcpu:
                if r[1] is not None:
                    off_index[id(r)] = k
                    k += 1
        data = {
            'v': 1,
            'mode': self.mode,
            'caps': self.caps,
            't0': t0,
            'btf': len(self.rettypes) if self.rettypes is not None else 0,
            'strings': self.strings,
            'tasks': tasks,
            'n': n,
            'cols': cols,
            'errs': [[remap[r[0]]] + r[1:] for r in self.err_rows],
            'chains': [[remap[c[0]], remap[c[1]]] + c[2:] for c in self.chains],
            'errnames': errnames,
            'offcpu': [[r[0], rel(r[1]), rel(r[2]), r[3], r[4], r[5], r[6] if r[6] is not None else -1,
                        r[7] if r[7] is not None else -1, r[8], r[9]]
                       for r in self.offcpu if timed and r[1] is not None],
            'resched': [[r[0], rel(r[1]), rel(r[2])] + r[3:] for r in self.resched] if timed else [],
            # [waker_task, ts, target_task, ctx, waker_slice, offcpu_index|-1]
            'wakeups': [[w[0], rel(w[1]), w[2], w[3], remap[w[4]] if w[4] >= 0 else -1, off_index.get(id(w[5]), -1)]
                        for w in self.wake_events] if timed else [],
            'irqoff': [[r[0], rel(r[1]), rel(r[2])] for r in self.irqoff] if timed else [],
            # [task, kind(0 irqs-off/1 preempt-off/2 BH-off), start, end, start_slice, end_slice, max_depth, start_line, end_line]
            'sections': [[r[0], r[1], rel(r[2]), rel(r[3]), remap[r[4]] if r[4] >= 0 else -1,
                          remap[r[5]] if r[5] >= 0 else -1, r[6], r[7], r[8]]
                         for r in self.sections if r[3] > r[2]] if timed else [],
            'atomic': [[remap[a[0]], a[1]] for a in self.atomic],
            'preempt_base': self.preempt_base,
            'lost': [[r[0], r[2], r[3]] for r in self.lost],
            'stats': {k: (round(v, 3) if isinstance(v, float) else v) for k, v in self.stats.items()},
            'overhead': {'child': ovh[0], 'self': ovh[1], 'source': self.overhead_source} if ovh else None,
        }
        return data


def build_timeline(lines, rettypes=None, errno_map=None, verbose=False, overhead=None, rettype_resolver=None):
    b = TimelineBuilder(lines, rettypes=rettypes, errno_map=errno_map, verbose=verbose, overhead=overhead,
                        rettype_resolver=rettype_resolver)
    b.build()
    return b


if __name__ == '__main__':
    import json
    import sys
    path = sys.argv[1]
    vml = sys.argv[2] if len(sys.argv) > 2 else None
    db = None
    if vml:
        from fglib import btf
        db = btf.build_return_db(vml)
    with open(path, errors='replace') as f:
        lines = f.read().split('\n')
    b = build_timeline(lines, rettypes=db)
    d = b.to_data()
    s = json.dumps(d, separators=(',', ':'))
    print('mode', d['mode'], 'caps', {k: v for k, v in d['caps'].items() if v})
    print('stats', d['stats'], 'json bytes', len(s))
    print('chains', len(d['chains']), 'errs', len(d['errs']), 'offcpu', len(d['offcpu']), 'resched', len(d['resched']))
    fates = {}
    for c in d['chains']:
        fates[c[3]] = fates.get(c[3], 0) + 1
    print('fates(0 handled,1 user,2 escaped)', fates)
    for c in d['chains'][:12]:
        o, t = c[0], c[1]
        print('  %-28s -> %-28s errno=%d fate=%d len=%d verified=%d' % (
            d['strings'][d['cols']['name'][o]], d['strings'][d['cols']['name'][t]], c[2], c[3], c[4], c[5]))

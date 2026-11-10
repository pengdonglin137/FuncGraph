"""Import the patched uftrace stream export without round-tripping through ftrace text."""

from bisect import bisect_left, bisect_right
from collections import defaultdict
import json
import re
import time

from .model import FtraceLine
from .parser import _base36
from .timeline import (TimelineBuilder, _Frame, F_USER, F_BOUNDARY, F_ORPHAN, F_TRUNCATED,
                       F_HAS_LAT, F_IRQOFF, F_PREEMPT_SHIFT, F_RESCHED_SHIFT, RE_MAY_SLEEP,
                       RE_SCHED_SWITCH, CTX_TASK, CTX_HARDIRQ, CTX_SOFTIRQ)


SCHEMA = 'funcgraph.uftrace.v1'
HEX = re.compile(r'0x[0-9a-fA-F]{1,16}\Z')
CLOCK = re.compile(r'\[([^\]]+)\]')


class UftraceInputError(ValueError):
    pass


class UftraceLine(FtraceLine):
    __slots__ = ('space', 'boundary', 'source', 'record_type')

    def __init__(self, *, record_type, space=None, boundary=False, source=None, **kwargs):
        super().__init__(**kwargs)
        self.space = space
        self.boundary = boundary
        self.source = source
        self.record_type = record_type


def _uint(value, maximum=(1 << 64) - 1):
    return type(value) is int and 0 <= value <= maximum


def _check_record(record):
    kind, space = record.get('type'), record.get('space')
    if kind not in ('entry', 'exit', 'event', 'lost') or not _uint(record.get('time_ns')):
        raise UftraceInputError('invalid record type or timestamp')
    if space is None and kind == 'event' and record.get('origin') == 'perf':
        return
    if space not in ('user', 'kernel'):
        raise UftraceInputError('expected explicit user or kernel address space')
    cpu, tid = record.get('cpu'), record.get('tid')
    if not (_uint(cpu, (1 << 31) - 1) if space == 'kernel' else cpu is None):
        raise UftraceInputError('invalid CPU for address space')
    if not (space == 'kernel' and kind == 'lost' and tid is None) and not _uint(tid, (1 << 31) - 1):
        raise UftraceInputError('invalid thread ID')
    if kind == 'lost' and (not _uint(record.get('count')) and record.get('count') != -1):
        raise UftraceInputError('invalid loss count')
    if kind in ('entry', 'exit'):
        if not _uint(record.get('depth'), 4096):
            raise UftraceInputError('invalid native depth')
        if not isinstance(record.get('name'), str) or not isinstance(record.get('address'), str) or not HEX.fullmatch(record['address']):
            raise UftraceInputError('invalid function name or address')
    for field in ('retval', 'retaddr'):
        if field in record and (not isinstance(record[field], str) or not HEX.fullmatch(record[field])):
            raise UftraceInputError('invalid ' + field)
    if 'args' in record and (not isinstance(record['args'], list) or
                             not all(isinstance(value, str) and HEX.fullmatch(value) for value in record['args'])):
        raise UftraceInputError('invalid arguments')
    for field in ('common_flags', 'common_preempt_count', 'overrun'):
        if field in record and not _uint(record[field]):
            raise UftraceInputError('invalid ' + field)
    for field in ('name', 'comm', 'text', 'payload_hex'):
        if field in record and not isinstance(record[field], str):
            raise UftraceInputError('invalid ' + field)
    if 'source' in record:
        source = record['source']
        if not (isinstance(source, dict) and isinstance(source.get('file'), str) and source['file'] and
                _uint(source.get('line'), (1 << 31) - 1) and source['line']):
            raise UftraceInputError('invalid recorded source position')


def read_export(stream):
    """Return validated metadata and records, retaining export order for timestamp ties."""
    metadata = None
    records = []
    completed = False
    stream_times = {}
    for lineno, line in enumerate(stream, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            if not isinstance(record, dict) or completed:
                raise UftraceInputError('unexpected data after footer' if completed else 'expected a JSON object')
            if metadata is None:
                if (record.get('type'), record.get('schema'), record.get('order'),
                    record.get('timestamp_unit'), record.get('depth_basis')) != (
                        'metadata', SCHEMA, 'streams', 'ns', 'address-space'):
                    raise UftraceInputError('expected a funcgraph.uftrace.v1 stream header')
                settings, clock = record.get('kernel_settings'), record.get('clock')
                if (not isinstance(settings, dict) or not all(isinstance(value, str) for value in settings.values()) or
                        (clock is not None and not isinstance(clock, str))):
                    raise UftraceInputError('invalid clock or kernel settings')
                metadata = record
            elif record.get('type') == 'end':
                if record.get('complete') is not True:
                    raise UftraceInputError('uftrace export did not complete')
                completed = True
            else:
                _check_record(record)
                space = record.get('space')
                timestamp = record['time_ns']
                if space is not None:
                    stream_key = (space, record['cpu'] if space == 'kernel' else record['tid'])
                    last = stream_times.get(stream_key, 0)
                    if space == 'user' and record['type'] == 'lost' and timestamp == 0:
                        timestamp = last
                    if timestamp < last:
                        raise UftraceInputError('timestamp decreased within a native stream')
                    stream_times[stream_key] = timestamp
                records.append((timestamp, record))
        except (ValueError, RecursionError) as error:
            raise UftraceInputError(f'uftrace JSONL line {lineno}: {error}') from error
    if not completed:
        raise UftraceInputError('missing complete uftrace export footer')
    records.sort(key=lambda item: item[0])
    if records and records[-1][0] - records[0][0] >= 2 ** 53:
        raise UftraceInputError('recorded time span exceeds exact browser nanosecond range')
    return metadata, [record for _, record in records]


def _display(value):
    return ''.join(char if char.isprintable() else f'\\u{ord(char):04x}' for char in value)


def _latency(record):
    raw = record.get('common_flags')
    preempt = record.get('common_preempt_count')
    if raw is None or preempt is None:
        return 0, None, None
    context = CTX_HARDIRQ if raw & 8 else CTX_SOFTIRQ if raw & 16 else CTX_TASK
    resched = 6 if raw & 6 == 6 else 1 if raw & 4 else 4 if raw & 2 else 0
    flags = (F_HAS_LAT | context | (F_IRQOFF if raw & 1 else 0) |
             (min(preempt, 15) << F_PREEMPT_SHIFT) | (resched << F_RESCHED_SHIFT))
    lat = ('d' if raw & 1 else '.') + '.n..l.b'[resched] + '.sh'[context] + format(min(preempt, 15), 'x')
    return flags, context, lat


class UftraceTimeline(TimelineBuilder):
    """Pair each native stack first; link only contained same-thread kernel subtrees."""

    def __init__(self, metadata, records, **options):
        super().__init__([], **options)
        self.metadata = metadata
        self.records = records
        self.spaces = {record['space'] for record in records if record['type'] in ('entry', 'exit')}
        clock, settings = metadata['clock'], metadata['kernel_settings']
        selected = CLOCK.search(settings.get('trace_clock', ''))
        self.aligned = clock in ('mono', 'mono_raw', 'boot') and selected is not None and selected[1] == clock
        self.mixed = len(self.spaces) == 2
        self.parsed = []
        self.record_slices = {}
        self.positions = {}
        self.position_ids = {}
        self.native_depths = []
        self.addresses = []
        self.joined = 0
        self.detached = 0

    def _close_frame(self, frame, timestamp):
        index = frame.idx
        self.s_flags[index] |= F_TRUNCATED
        self.s_end[index] = max(self.s_start[index], timestamp)

    def _call(self, record, task, stack, lineno, flags, context):
        depth, timestamp = record['depth'], record['time_ns']
        user = record['space'] == 'user'
        address = int(record['address'], 16)
        is_exit = record['type'] == 'exit'
        retval = record.get('retval') if self.metadata['kernel_settings'].get('options/funcgraph-retval') != '0' else None
        if user:
            retval = None
        while stack and stack[-1].depth > depth:
            self._close_frame(stack.pop(), timestamp)
        if stack and stack[-1].depth == depth:
            frame = stack.pop()
            if is_exit and self.addresses[frame.idx] == address and not record.get('overrun'):
                index = frame.idx
                self.s_end[index] = timestamp
                self.s_ret[index] = retval
                self.s_eline[index] = lineno
                self.record_slices[lineno] = index
                return
            self._close_frame(frame, timestamp)
        name = _display(record['name']) or record['address']
        parent = stack[-1].idx if stack else -1
        flags |= F_USER if user else 0
        flags |= F_ORPHAN if is_exit else 0
        args = (', '.join(record['args']) or '[]') if 'args' in record else record.get('payload_hex')
        caller = self._s(record.get('retaddr')) if not user else -1
        self.caps['args'] |= bool(args)
        self.caps['retaddr'] |= caller >= 0
        index = self._new_slice(task, depth, timestamp, timestamp if is_exit else None,
                                name, None, lineno, flags, parent, retval if is_exit else None, caller, self._s(args),
                                namespace='user' if user else None)
        self.native_depths.append(depth)
        self.addresses.append(address)
        self.record_slices[lineno] = index
        if user:
            source = record.get('source')
            if source:
                position = (_display(source['file']), source['line'], '')
                self.positions[lineno] = self.position_ids.setdefault(position, len(self.position_ids))
        elif not is_exit and flags & F_HAS_LAT and RE_MAY_SLEEP.match(name):
            reasons = (1 if flags & F_IRQOFF else 0) | (2 if ((flags >> F_PREEMPT_SHIFT) & 15) > self.preempt_base else 0)
            if context != CTX_TASK:
                reasons |= 4
            if reasons:
                self.atomic.append([index, reasons])
        if is_exit:
            self.s_eline[index] = lineno
        else:
            stack.append(_Frame(index, depth, name, context if context is not None else CTX_TASK))

    def build(self, enable_fold=False, include_text=True):
        start = time.monotonic()
        self.caps.update(abs=True, proc=True, duration=True)
        preempts = [min(record['common_preempt_count'], 15) for record in self.records
                    if record['type'] == 'entry' and record.get('space') == 'kernel' and
                    'common_preempt_count' in record and record.get('common_flags', 0) & 24 == 0]
        self.preempt_base = min(preempts, default=0)
        user_comms = {record['tid']: _display(record['comm']) for record in self.records
                      if record.get('space') == 'user' and record.get('comm')}
        user_stacks = defaultdict(list)
        user_last = {}
        for lineno, record in enumerate(self.records, 3):
            space, kind, timestamp = record.get('space'), record['type'], record['time_ns']
            if space is None:
                continue
            tid, cpu = record['tid'], record['cpu']
            if kind == 'lost':
                self.lost.append([cpu, timestamp, record['count'] if record['count'] >= 0 else None, lineno])
                if space == 'kernel':
                    for task in self.task_list:
                        if task.last_cpu == cpu:
                            while task.stack:
                                self._close_frame(task.stack.pop(), task.last_ts)
                            self._close_sections(task, task.last_ts, lineno)
                else:
                    while user_stacks[tid]:
                        self._close_frame(user_stacks[tid].pop(), user_last.get(tid, timestamp))
                continue
            comm = user_comms.get(tid)
            task = self._task(tid, comm, cpu, namespace='user' if space == 'user' and self.mixed and not self.aligned else None)
            self._cur_cpu = cpu if cpu is not None else -1
            if space == 'user':
                user_last[tid] = timestamp
                if kind in ('entry', 'exit'):
                    self._call(record, task, user_stacks[tid], lineno, 0, None)
                continue
            flags, context, lat = _latency(record)
            self.caps['cpu'] = True
            self.caps['latency'] |= lat is not None
            previous = task.last_ts
            self._resolve_switch_in(task, cpu, timestamp)
            self._track_run(task, cpu, timestamp)
            if lat is not None and kind != 'event':
                self._track_lat(task, lat, flags, timestamp, lineno, previous)
            if kind == 'event':
                self._event(record, task, timestamp, cpu, context)
            else:
                if record.get('overrun'):
                    while task.stack:
                        self._close_frame(task.stack.pop(), previous if previous is not None else timestamp)
                self._call(record, task, task.stack, lineno, flags, context)
        for tid, stack in user_stacks.items():
            for frame in stack:
                self._close_frame(frame, user_last[tid])
        for task in self.task_list:
            for frame in task.stack:
                self._close_frame(frame, task.last_ts)
        if self.mixed and self.aligned:
            self._join()
        self.caps['retval'] = any(value is not None for value in self.s_ret)
        self._finish(True)
        self.stats['build_time'] = time.monotonic() - start
        self.stats.update(user_kernel_boundaries=self.joined, detached_kernel_roots=self.detached)
        self.parsed = self._text_lines(enable_fold, include_text)
        return self

    def _event(self, record, task, timestamp, cpu, context):
        name, payload = record.get('name', ''), record.get('text', '')
        if name == 'sched:sched_switch':
            match = RE_SCHED_SWITCH.search('sched_switch: ' + payload)
            if match:
                prev_comm, prev_pid, state, next_comm, next_pid = match.groups()
                self.caps['switch_markers'] = True
                self.caps['sched_events'] = True
                self.pending_state[int(prev_pid)] = state
                self._switch(cpu, int(prev_pid), int(next_pid), prev_comm, next_comm, timestamp)
                next_task = self._task(int(next_pid), next_comm, cpu)
                self._resolve_switch_in(next_task, cpu, timestamp)
                self._track_run(next_task, cpu, timestamp)
        elif name in ('sched:sched_waking', 'sched:sched_wakeup', 'sched:sched_wakeup_new'):
            self._on_comment(name.split(':', 1)[1] + ': ' + payload, task, timestamp, cpu, context)

    def _join(self):
        users, kernel_roots = defaultdict(list), []
        roots = []
        for index, parent in enumerate(self.s_parent):
            if self.s_flags[index] & F_USER:
                users[self.s_task[index]].append(index)
            root = roots[parent] if parent >= 0 else index
            roots.append(root)
            if root == index and not self.s_flags[index] & F_USER:
                kernel_roots.append(index)
        starts = {task: [self.s_start[index] for index in indices] for task, indices in users.items()}
        latest_end = {task: [] for task in users}
        for task, indices in users.items():
            end = 0
            for index in indices:
                end = max(end, self.s_end[index])
                latest_end[task].append(end)
        offsets, lanes = {}, {}
        for index in kernel_roots:
            task_id = self.s_task[index]
            if task_id not in users:
                continue
            start, end = self.s_start[index], self.s_end[index]
            times = starts[task_id]
            pos = bisect_right(times, start) - 1
            parent = users[task_id][pos] if pos >= 0 else -1
            while parent >= 0 and self.s_end[parent] <= start:
                parent = self.s_parent[parent]
            if (parent >= 0 and end > start and end <= self.s_end[parent] and
                    not (self.s_flags[parent] | self.s_flags[index]) & (F_ORPHAN | F_TRUNCATED) and
                    self.native_depths[index] == 0 and bisect_left(times, end) == pos + 1):
                self.s_parent[index] = parent
                offsets[index] = self.s_depth[parent] + 1
                self.s_flags[index] |= F_BOUNDARY
                self.joined += 1
            else:
                last = bisect_right(times, end) - 1
                if last >= 0 and latest_end[task_id][last] >= start:
                    task = self.task_list[task_id]
                    lanes[index] = self._task(task.pid, task.comm, None, namespace='unjoined').tid
                    self.detached += 1
        for index, root in enumerate(roots):
            if root in offsets:
                self.s_depth[index] += offsets[root]
            if root in lanes:
                self.s_task[index] = lanes[root]

    def _err_value(self, index):
        if self.rettypes is None or self.s_flags[index] & F_USER:
            return None, False, False
        name = self.strings[self.s_name[index]]
        if self.rettypes.lookup(name)[0] is None:
            return None, False, False
        return super()._err_value(index)

    def _text_lines(self, enable_fold, include_text):
        clock = self.metadata['clock'] or 'unknown'
        note = ('shared ' + clock + ' clock' if self.aligned else 'clock alignment not verified') if self.mixed else clock + ' clock'
        header = '# uftrace · ' + ' + '.join(sorted(self.spaces, reverse=True)) + ' · ' + note
        if self.mixed and not self.aligned:
            header += ' · address spaces shown separately'
        lines = [UftraceLine(raw_line=header, record_type='metadata'), FtraceLine(raw_line='# timestamp | CPU | task | space | call / event')]
        if not include_text:
            return lines
        fold_ids, ancestors = {}, {}
        for lineno, record in enumerate(self.records, 3):
            kind, space = record['type'], record.get('space')
            tid, cpu = record.get('tid'), record.get('cpu')
            seconds, nanos = divmod(record['time_ns'], 1000000000)
            tag = ('U' if space == 'user' else 'K') if self.mixed and space else ' '
            prefix = f'{seconds:>7}.{nanos:09d} | {str(cpu) if cpu is not None else "–":>3} | {str(tid) if tid is not None else "–":>7} | {tag} | '
            index = self.record_slices.get(lineno)
            name = args = fold_id = fold_type = depth = duration = source = comm = None
            path = ''
            boundary = False
            if index is not None:
                flags = self.s_flags[index]
                name = self.strings[self.s_name[index]]
                comm = self.task_list[self.s_task[index]].comm
                depth = self.s_depth[index]
                parent = self.s_parent[index]
                boundary = kind == 'entry' and bool(flags & F_BOUNDARY)
                if kind == 'entry':
                    args = self.strings[self.s_args[index]] if self.s_args[index] >= 0 else None
                    detail = ' (' + args + ')' if args else '()'
                    body = name + detail + ' {'
                    if record.get('retaddr'):
                        body += ' /* caller address ' + record['retaddr'] + ' */'
                    pos = record.get('source')
                    if space == 'user' and pos:
                        source = (_display(pos['file']), pos['line'])
                else:
                    retval = self.s_ret[index]
                    duration = (self.s_end[index] - self.s_start[index]) / 1000 if not flags & F_ORPHAN else None
                    elapsed = f' · {duration:.3f} µs' if duration is not None else ' · entry not recorded'
                    body = '} /* ' + name + elapsed + (' · raw return ' + retval if retval is not None else '') + ' */'
                if enable_fold:
                    path = ancestors.get(parent, '')
                    if not flags & (F_ORPHAN | F_TRUNCATED):
                        fold_id = fold_ids.setdefault(index, _base36(index + 1))
                        fold_type = kind
                    elif path:
                        fold_type, fold_id = 'inner', path.rsplit(' ', 1)[-1]
                    if kind == 'entry':
                        ancestors[index] = (path + ' ' + fold_id).strip() if fold_type == 'entry' else path
                body = '  ' * depth + body
            elif kind == 'lost':
                body = f'/* Lost {record["count"] if record["count"] >= 0 else "unknown number of"} records */'
                if space == 'user' and record['time_ns'] == 0:
                    prefix = '     timestamp ? |   – | ' + f'{tid:>7} | {tag} | '
            else:
                body = '/* ' + _display(record.get('name', 'perf event')) + ': ' + _display(record.get('text', '')) + ' */'
            flags, context, lat = _latency(record) if space == 'kernel' else (0, None, None)
            lines.append(UftraceLine(raw_line=prefix + body, raw_func_name=name if kind == 'entry' else None,
                                     display_func_name=name, cpu=cpu, pid=tid, comm=comm, params=args,
                                     duration=duration, sort_duration=duration, fold_id=fold_id, fold_type=fold_type,
                                     fold_ancestors=path, call_depth=depth, space=space, boundary=boundary, source=source, record_type=kind,
                                     irqs_off=lat[0] if lat else None, need_resched=lat[1] if lat else None,
                                     hardirq_softirq=lat[2] if lat else None, preempt_depth=lat[3] if lat else None))
        return lines

    def to_data(self):
        data = super().to_data(preserve_zero=True)
        data['uftrace'] = {'clock': self.metadata['clock'], 'aligned': self.aligned, 'origin_ns': str(data['t0']),
                          'spaces': sorted(self.spaces), 'kernel_settings': self.metadata['kernel_settings']}
        if self.positions:
            data['sources'] = {'functions': {}, 'callers': {}, 'user_lines': self.positions,
                               'user_positions': list(self.position_ids)}
        return data

"""Import the patched uftrace stream export without round-tripping through ftrace text."""

from bisect import bisect_left, bisect_right
from collections import defaultdict
import json
import re
import time

from .model import FtraceLine
from .btf import format_scalar
from .parser import _base36
from .timeline import (TimelineBuilder, _Frame, F_USER, F_BOUNDARY, F_ORPHAN, F_TRUNCATED,
                       F_HAS_LAT, F_IRQOFF, F_BHOFF, F_PREEMPT_SHIFT, RE_MAY_SLEEP,
                       RE_SCHED_SWITCH, RE_COMPARATOR, _strip_suffix, CTX_TASK)


SCHEMA = 'funcgraph.uftrace.v1'
HEX = re.compile(r'0x[0-9a-fA-F]{1,16}\Z')
SYMBOL = re.compile(r'[A-Za-z0-9_.$]+\Z')
MODULE = re.compile(r'[A-Za-z0-9_-]+\Z')
SCALAR = re.compile(r'(?:-?[0-9]{1,20}|0x[0-9a-fA-F]{1,16})\Z')
CLOCK = re.compile(r'\[([^\]]+)\]')
KERNEL_VERSION = re.compile(r'^([0-9]+)\.([0-9]+)(?:[.+-]|$)')
LIFECYCLE = {'sched:sched_process_exec': 'exec', 'sched:sched_process_exit': 'exit'}


class UftraceInputError(ValueError):
    pass


class UftraceLine(FtraceLine):
    __slots__ = ('space', 'boundary', 'source', 'source_kind', 'record_type')

    def __init__(self, *, record_type, space=None, boundary=False, source=None, source_kind=None, **kwargs):
        super().__init__(**kwargs)
        self.space = space
        self.boundary = boundary
        self.source = source
        self.source_kind = source_kind
        self.record_type = record_type


def _uint(value, maximum=(1 << 64) - 1):
    return type(value) is int and 0 <= value <= maximum


def _source_position(source):
    return (isinstance(source, dict) and isinstance(source.get('file'), str) and source['file'] and
            _uint(source.get('line'), (1 << 31) - 1) and source['line'] > 0)


def _check_record(record):
    kind, space = record.get('type'), record.get('space')
    if kind not in ('entry', 'exit', 'event', 'lost') or not _uint(record.get('time_ns')):
        raise UftraceInputError('invalid record type or timestamp')
    if (space is None and kind == 'event' and record.get('origin') == 'perf' and
            'retaddr_symbol' not in record and 'user_callsite' not in record):
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
    if 'retaddr_symbol' in record:
        symbol = record['retaddr_symbol']
        if not (space == 'kernel' and kind == 'entry' and 'retaddr' in record and
                isinstance(symbol, dict) and isinstance(symbol.get('name'), str) and
                SYMBOL.fullmatch(symbol['name']) and isinstance(symbol.get('address'), str) and
                HEX.fullmatch(symbol['address']) and _uint(symbol.get('offset')) and
                _uint(symbol.get('size')) and 0 < symbol['offset'] <= symbol['size'] and
                0 < int(symbol['address'], 16) <= int(record['retaddr'], 16) and
                int(symbol['address'], 16) + symbol['offset'] == int(record['retaddr'], 16) and
                int(symbol['address'], 16) + symbol['size'] < (1 << 64) and
                symbol.get('size_basis') == 'next_symbol' and symbol.get('source') == 'saved_kallsyms' and
                ('module' not in symbol or isinstance(symbol['module'], str) and MODULE.fullmatch(symbol['module']))):
            raise UftraceInputError('invalid captured return-address symbol')
    if 'args' in record and (not isinstance(record['args'], list) or
                             not all(isinstance(value, str) and HEX.fullmatch(value) for value in record['args'])):
        raise UftraceInputError('invalid arguments')
    if 'user_args' in record and ((space, kind) != ('user', 'entry') or
                                  not isinstance(record['user_args'], list) or not record['user_args'] or
                                  not all(isinstance(value, str) and SCALAR.fullmatch(value) for value in record['user_args'])):
        raise UftraceInputError('invalid user argument values')
    if 'user_retval' in record and ((space, kind) != ('user', 'exit') or
                                    not isinstance(record['user_retval'], str) or not SCALAR.fullmatch(record['user_retval'])):
        raise UftraceInputError('invalid user return value')
    for field in ('common_flags', 'common_preempt_count', 'overrun'):
        if field in record and not _uint(record[field]):
            raise UftraceInputError('invalid ' + field)
    for field in ('name', 'comm', 'text', 'payload_hex'):
        if field in record and not isinstance(record[field], str):
            raise UftraceInputError('invalid ' + field)
    if 'source' in record and not _source_position(record['source']):
        raise UftraceInputError('invalid recorded source position')
    if 'user_callsite' in record:
        site = record['user_callsite']
        if not (space == 'user' and kind == 'event' and record.get('name') == 'callsite' and
                isinstance(site, dict) and isinstance(site.get('retaddr'), str) and
                HEX.fullmatch(site['retaddr']) and int(site['retaddr'], 16) > 0 and
                ('name' not in site or isinstance(site['name'], str) and bool(site['name'])) and
                ('source' not in site or _source_position(site['source']))):
            raise UftraceInputError('invalid recorded user call site')
        if 'symbol' in site:
            symbol = site['symbol']
            if not (site.get('name') and isinstance(symbol, dict) and
                    isinstance(symbol.get('address'), str) and HEX.fullmatch(symbol['address']) and
                    _uint(symbol.get('offset')) and _uint(symbol.get('size')) and
                    symbol['offset'] < symbol['size'] and
                    int(symbol['address'], 16) + symbol['offset'] == int(site['retaddr'], 16) - 1):
                raise UftraceInputError('invalid recorded user caller symbol')


def _caller_info(record):
    symbol = record.get('retaddr_symbol')
    if not symbol:
        return None
    module = ' [' + symbol['module'] + ']' if symbol.get('module') else ''
    return f'{symbol["name"]}+0x{symbol["offset"]:x}/0x{symbol["size"]:x}{module}'


def _user_caller_info(site):
    symbol = site.get('symbol')
    if symbol:
        return f'{_display(site["name"])}+0x{symbol["offset"]:x}/0x{symbol["size"]:x}'
    if site.get('name'):
        return _display(site['name']) + ' (offset unavailable)'
    return f'0x{int(site["retaddr"], 16) - 1:x} (symbol unavailable)'


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


def _latency(record, lazy_resched):
    raw = record.get('common_flags')
    preempt = record.get('common_preempt_count')
    if raw is None or preempt is None or raw > 0xff or preempt > 0xff:
        return 0, None, None
    irq, bh = bool(raw & 0x01), bool(raw & 0x80)
    nmi, hard, soft = bool(raw & 0x40), bool(raw & 0x08), bool(raw & 0x10)
    irq_char = '.dbD'[irq + 2 * bh]
    if irq_char == '.' and lazy_resched is False and raw & 0x02:
        irq_char = 'X'
    resched = bool(raw & 0x04) + 2 * bool(raw & 0x20) + 4 * bool(lazy_resched and raw & 0x02)
    ctx_char = ('Z' if hard else 'z') if nmi else '.hsH'[hard + 2 * soft]
    lat = irq_char + '.npNlbLB'[resched] + ctx_char + f'{preempt & 0xf:x}{preempt >> 4:x}'
    flags, context = TimelineBuilder._lat_flags(lat)
    return flags, context, lat


class UftraceTimeline(TimelineBuilder):
    """Pair each native stack first; link only contained same-thread kernel subtrees."""

    def __init__(self, metadata, records, **options):
        super().__init__([], **options)
        self.metadata = metadata
        self.records = records
        self.spaces = {record['space'] for record in records if record['type'] in ('entry', 'exit') or
                       (record.get('space') == 'kernel' and record['type'] == 'event')}
        clock, settings = metadata['clock'], metadata['kernel_settings']
        version = KERNEL_VERSION.match(settings.get('kernel_release', ''))
        self.lazy_resched = tuple(map(int, version.groups())) >= (6, 13) if version else None
        selected = CLOCK.search(settings.get('trace_clock', ''))
        self.aligned = clock in ('mono', 'mono_raw', 'boot') and selected is not None and selected[1] == clock
        self.mixed = len(self.spaces) == 2
        self.parsed = []
        self.record_slices = {}
        self.positions = {}
        self.callsite_positions = {}
        self.position_ids = {}
        self.user_details = {}
        self.kernel_details = {}
        self.argument_names = {}
        self.kernel_types = {}
        self.caller_symbols = {}
        self.lifecycle = []
        self.native_depths = []
        self.addresses = []
        self.joined = 0
        self.detached = 0

    def _close_frame(self, frame, timestamp):
        index = frame.idx
        self.s_flags[index] |= F_TRUNCATED
        self.s_end[index] = max(self.s_start[index], timestamp)

    def _call(self, record, task, stack, lineno, flags, context, user_site=None):
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
                if user:
                    self._user_exit(record, self.s_line[index])
                return
            self._close_frame(frame, timestamp)
        name = _display(record['name']) or record['address']
        parent = stack[-1].idx if stack else -1
        flags |= F_USER if user else 0
        flags |= F_ORPHAN if is_exit else 0
        args = (', '.join(record['args']) or '[]') if 'args' in record else record.get('payload_hex')
        if user and is_exit:
            args = None
        caller_info = _caller_info(record) if not user else None
        caller = self._s(caller_info or record.get('retaddr')) if not user else -1
        if not user and 'retaddr' in record:
            self.kernel_details[lineno] = {'retaddr': record['retaddr']}
            if caller_info:
                self.caller_symbols[caller] = record['retaddr_symbol']
        self.caps['args'] |= bool(args)
        self.caps['retaddr'] |= caller >= 0
        index = self._new_slice(task, depth, timestamp, timestamp if is_exit else None,
                                name, None, lineno, flags, parent, retval if is_exit else None, caller, self._s(args),
                                namespace='user' if user else None)
        self.native_depths.append(depth)
        self.addresses.append(address)
        self.record_slices[lineno] = index
        if user:
            detail = self.user_details[lineno] = {'address': record['address']}
            if 'user_args' in record:
                detail['args'] = ', '.join(_display(value) for value in record['user_args'])
            if user_site:
                detail['callsite'] = {'retaddr': user_site['retaddr']}
                if 'name' in user_site:
                    detail['callsite']['name'] = _display(user_site['name'])
                if 'symbol' in user_site:
                    detail['callsite']['symbol'] = dict(user_site['symbol'])
                if 'source' in user_site:
                    site = user_site['source']
                    position = (_display(site['file']), site['line'], '')
                    self.callsite_positions[lineno] = self.position_ids.setdefault(position, len(self.position_ids))
            if is_exit:
                self._user_exit(record, lineno)
            source = record.get('source')
            if source:
                position = (_display(source['file']), source['line'], '')
                self.positions[lineno] = self.position_ids.setdefault(position, len(self.position_ids))
        elif not is_exit and flags & F_HAS_LAT and RE_MAY_SLEEP.match(name):
            reasons = ((1 if flags & F_IRQOFF else 0) | (2 if ((flags >> F_PREEMPT_SHIFT) & 15) > self.preempt_base else 0)
                       | (8 if flags & F_BHOFF else 0))
            if context != CTX_TASK:
                reasons |= 4
            if reasons:
                self.atomic.append([index, reasons])
        if is_exit:
            self.s_eline[index] = lineno
        else:
            stack.append(_Frame(index, depth, name, context if context is not None else CTX_TASK))

    def _user_exit(self, record, lineno):
        detail = self.user_details[lineno]
        if 'user_retval' in record:
            detail['retval'] = _display(record['user_retval'])
        if 'payload_hex' in record:
            detail['return_payload'] = _display(record['payload_hex'])

    def build(self, enable_fold=False, include_text=True):
        start = time.monotonic()
        self.caps.update(abs=True, proc=True, duration=True)
        preempts = (record['common_preempt_count'] & 0xf for record in self.records
                    if record['type'] == 'entry' and record.get('space') == 'kernel' and
                    record.get('common_flags', 256) < 256 and not record['common_flags'] & 0x58 and
                    record.get('common_preempt_count', 256) < 256)
        self.preempt_base = min(1, min(preempts, default=0))
        user_comms = {record['tid']: _display(record['comm']) for record in self.records
                      if record.get('space') == 'user' and record.get('comm')}
        user_stacks = defaultdict(list)
        user_last = {}
        user_sites = {}
        for lineno, record in enumerate(self.records, 3):
            space, kind, timestamp = record.get('space'), record['type'], record['time_ns']
            if space is None:
                continue
            tid, cpu = record['tid'], record['cpu']
            pending = user_sites.pop(tid, None) if space == 'user' else None
            if 'user_callsite' in record:
                user_sites[tid] = (timestamp, record['user_callsite'])
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
                    site = pending[1] if kind == 'entry' and pending and pending[0] + 1 == timestamp else None
                    self._call(record, task, user_stacks[tid], lineno, 0, None, site)
                continue
            flags, context, lat = _latency(record, self.lazy_resched)
            self.caps['cpu'] = True
            self.caps['latency'] |= lat is not None
            previous = task.last_ts
            self._resolve_switch_in(task, cpu, timestamp)
            self._track_run(task, cpu, timestamp)
            if lat is not None and kind != 'event':
                self._track_lat(task, lat, flags, timestamp, lineno, previous)
            if kind == 'event':
                self._event(record, task, timestamp, cpu, context)
                phase = LIFECYCLE.get(record.get('name'))
                if phase is not None:
                    self.lifecycle.append((task.tid, timestamp, phase, lineno))
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
        self._name_kernel_arguments()
        self.stats['build_time'] = time.monotonic() - start
        self.stats.update(user_kernel_boundaries=self.joined, detached_kernel_roots=self.detached)
        self.parsed = self._text_lines(enable_fold, include_text)
        return self

    def _name_kernel_arguments(self):
        if self.rettypes is None or not hasattr(self.rettypes, 'argument_names'):
            return
        counts = {}
        for index, flags in enumerate(self.s_flags):
            if flags & F_USER:
                continue
            values = self.records[self.s_line[index] - 3].get('args')
            name = self.s_name[index]
            counts[name] = max(counts.get(name, 0), len(values or ()))
        for name, count in counts.items():
            names = self.rettypes.argument_names(self.strings[name], count)
            if any(names):
                self.argument_names[name] = names
            if hasattr(self.rettypes, 'function_types'):
                types = self.rettypes.function_types(self.strings[name])
                if types:
                    self.kernel_types[name] = types

    def _formatted_arguments(self, index, values):
        name = self.s_name[index]
        names = self.argument_names.get(name, ())
        signature = self.kernel_types.get(name, {})
        formats = signature.get('args', ())
        count = signature.get('argc', len(values))
        result = []
        for position, value in enumerate(values[:count]):
            if position < len(formats):
                value = format_scalar(value, *formats[position])
            label = names[position] if position < len(names) and names[position] else f'slot{position + 1}'
            result.append(label + '=' + value)
        if count > len(values):
            result.append('…')
        return ', '.join(result)

    def _formatted_return(self, index):
        value = self.s_ret[index]
        signature = self.kernel_types.get(self.s_name[index], {})
        return format_scalar(value, *signature['ret'], returning=True) if value is not None and 'ret' in signature else value

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
        signature = self.rettypes.function_types(name) if hasattr(self.rettypes, 'function_types') else None
        returned = signature.get('ret') if signature else None
        if returned and self.s_ret[index] is not None:
            kind, bits = returned
            if kind not in ('sint', 'ptr') or kind == 'sint' and RE_COMPARATOR.search(_strip_suffix(name)):
                return None, True, False
            try:
                value = int(self.s_ret[index], 0) & ((1 << bits) - 1)
            except ValueError:
                return None, False, False
            if kind == 'ptr' and value == 0:
                return None, True, True
            if value & (1 << (bits - 1)):
                value -= 1 << bits
            return (value, True, False) if -4095 <= value <= -1 else (None, True, False)
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
        positions = tuple(self.position_ids)
        for lineno, record in enumerate(self.records, 3):
            kind, space = record['type'], record.get('space')
            tid, cpu = record.get('tid'), record.get('cpu')
            seconds, nanos = divmod(record['time_ns'], 1000000000)
            tag = ('U' if space == 'user' else 'K') if self.mixed and space else ' '
            prefix = f'{seconds:>7}.{nanos:09d} | {str(cpu) if cpu is not None else "–":>3} | {str(tid) if tid is not None else "–":>7} | {tag} | '
            index = self.record_slices.get(lineno)
            name = args = fold_id = fold_type = depth = duration = source = source_kind = comm = func_info = None
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
                    if space == 'user':
                        args = self.user_details[lineno].get('args') or ('raw bytes: ' + args if args else None)
                    else:
                        names = self.argument_names.get(self.s_name[index], ())
                        if (names or self.s_name[index] in self.kernel_types) and record.get('args'):
                            args = self._formatted_arguments(index, record['args'])
                    detail = ' (' + args + ')' if args else '()'
                    body = name + detail + ' {'
                    if space == 'user':
                        body += ' /* recorded address ' + record['address'] + ' */'
                    if record.get('retaddr'):
                        func_info = _caller_info(record)
                        body += ' /* ' + (func_info or 'caller address ' + record['retaddr']) + ' */'
                    pos = record.get('source')
                    if space == 'user' and pos:
                        source = (_display(pos['file']), pos['line'])
                        source_kind = 'user function entry'
                    if space == 'user' and 'callsite' in self.user_details[lineno]:
                        body += ' /* caller site ' + _user_caller_info(self.user_details[lineno]['callsite']) + ' */'
                        position_id = self.callsite_positions.get(lineno)
                        if position_id is not None:
                            source = positions[position_id][:2]
                            source_kind = 'user call site (recorded return address - 1)'
                else:
                    retval = self._formatted_return(index)
                    duration = (self.s_end[index] - self.s_start[index]) / 1000 if not flags & F_ORPHAN else None
                    elapsed = f' · {duration:.3f} µs' if duration is not None else ' · entry not recorded'
                    typed = 'ret' in self.kernel_types.get(self.s_name[index], {})
                    returned = (' · return ' if typed else ' · raw return ') + retval if retval is not None else ''
                    if space == 'user':
                        detail = self.user_details[self.s_line[index]]
                        if 'retval' in detail:
                            returned = ' · return ' + detail['retval']
                        elif 'return_payload' in detail:
                            returned = ' · raw return bytes: ' + detail['return_payload']
                    body = '} /* ' + name + elapsed + returned + ' */'
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
            _, _, lat = _latency(record, self.lazy_resched) if space == 'kernel' else (0, None, None)
            lines.append(UftraceLine(raw_line=prefix + body, raw_func_name=name if kind == 'entry' else None,
                                     expandable=bool(func_info), func_info=func_info,
                                     display_func_name=name, cpu=cpu, pid=tid, comm=comm, params=args,
                                     duration=duration, sort_duration=duration, fold_id=fold_id, fold_type=fold_type,
                                     fold_ancestors=path, call_depth=depth, space=space, boundary=boundary, source=source,
                                     source_kind=source_kind, record_type=kind,
                                     irqs_off=lat[0] if lat else None, need_resched=lat[1] if lat else None,
                                     hardirq_softirq=lat[2] if lat else None, preempt_depth=lat[3] if lat else None))
        return lines

    def to_data(self):
        data = super().to_data()
        data['uftrace'] = {'clock': self.metadata['clock'], 'aligned': self.aligned, 'origin_ns': str(data['t0']),
                          'spaces': sorted(self.spaces), 'kernel_settings': self.metadata['kernel_settings']}
        if self.user_details:
            data['uftrace']['user_details'] = self.user_details
        if self.kernel_details:
            data['uftrace']['kernel_details'] = self.kernel_details
        if self.argument_names:
            data['uftrace']['kernel_arg_names'] = self.argument_names
        if self.kernel_types:
            data['uftrace']['kernel_types'] = self.kernel_types
        if self.caller_symbols:
            data['uftrace']['caller_symbols'] = self.caller_symbols
        if self.lifecycle:
            data['uftrace']['lifecycle'] = [[task, timestamp - data['t0'], phase, line]
                                           for task, timestamp, phase, line in self.lifecycle]
        if self.positions or self.callsite_positions:
            data['sources'] = {'functions': {}, 'callers': {}, 'user_lines': self.positions,
                               'user_positions': list(self.position_ids)}
            if self.callsite_positions:
                data['sources']['user_callsites'] = self.callsite_positions
        return data
